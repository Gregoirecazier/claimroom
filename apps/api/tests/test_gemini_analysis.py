from __future__ import annotations

import hashlib
import json
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from claim_api.analysis import AnalysisError, AnalysisService, build_analysis_input, evaluate_gates, fixture_output, validate_output
from claim_api.gemini_analysis import GEMINI_SCHEMA_FIELDS, GeminiClaimsAnalyzer, gemini_response_schema
from claim_api.models import AnalysisRunRequest, DamageEstimate, EvidenceView, MediaAnalysis
from claim_api.evidence_service import EvidenceService, InvalidEvidenceUploadError
from claim_api.storage import StorageAdapterError, SupabaseStorageAdapter
from test_analysis import ACTOR, MemoryAnalysisRepository, make_case
from test_evidence_service import FakeStorage, MemoryEvidenceRepository, _upload_request


def case_with_media():
    case = make_case()
    for mime, kind, data in [("image/jpeg", "damage_photo", b"photo"), ("video/mp4", "cctv_video", b"video")]:
        eid = uuid4()
        case.evidence.append(EvidenceView(id=eid, case_id=case.id, kind=kind,
            storage_path=f"{case.id}/uploads/{eid}", source_kind="handler_upload", mode="live",
            mime_type=mime, byte_size=len(data), client_sha256=hashlib.sha256(data).hexdigest(),
            checksum_status="client_declared", received_at=case.created_at))
    return case


def output_for(snapshot):
    output = fixture_output(snapshot)
    photo, video = snapshot.evidence
    output.media_analysis = MediaAnalysis.model_validate({
        "analyzed_evidence_ids": [str(photo.id), str(video.id)], "summary": "Analyse conjointe.",
        "observations": [{"description": "Collision visible", "confidence": "medium", "citations": [{"evidence_id": str(video.id), "timestamp_seconds": 2.5}]}],
        "plates": [{"description": "Plaque floue", "confidence": "low", "citations": [{"evidence_id": str(video.id), "timestamp_seconds": 2.5}], "vehicle": "A", "plate": None, "legibility": "unreadable", "role": "unknown"}],
        "damages": [{"description": "Pare-chocs déformé", "confidence": "medium", "citations": [{"evidence_id": str(photo.id), "timestamp_seconds": None}], "vehicle": "A", "affected_parts": ["pare-chocs"], "severity": "moderate", "accident_link": "uncertain", "estimate": {"minimum_minor": 50000, "maximum_minor": 100000, "currency": "EUR", "assumptions": "Indicatif, dégâts cachés exclus"}}],
        "liability": {"likely_responsible": "undetermined", "reasoning": "Signalisation hors champ", "confidence": "low", "citations": [], "limitations": ["Signalisation absente"], "vehicle_assessments": [{"vehicle": "A", "role": "unknown", "assessment": "undetermined", "reasoning": "Signalisation hors champ", "confidence": "low", "citations": [{"evidence_id": str(video.id), "timestamp_seconds": 2.5}]}]},
        "cross_evidence_consistency": "Véhicules à rapprocher avec prudence", "limitations": ["Pas de devis"]})
    return output


class Response(BytesIO):
    def __init__(self, value, headers=None):
        super().__init__(json.dumps(value).encode())
        self.headers = headers or {}


class GeminiServer:
    def __init__(self, output, *, fail=None):
        self.output, self.fail = output, fail
        self.requests, self.deleted, self.counter = [], [], 0
        self.generation = None
        self.generation_calls = 0

    def __call__(self, request, timeout):
        self.requests.append(request)
        url, method = request.full_url, request.method
        if method == "DELETE":
            self.deleted.append(url)
            return Response({})
        if ":generateContent" in url:
            self.generation_calls += 1
            self.generation = json.loads(request.data)
            if self.fail == "unavailable" or (self.fail == "unavailable_once" and self.generation_calls == 1):
                raise HTTPError(url, 503, "temporarily unavailable", {}, None)
            if self.fail in {"rate", "bad_request", "schema_bad_request", "denied", "missing_model"}:
                if self.fail != "schema_bad_request" or "responseJsonSchema" in self.generation["generationConfig"]:
                    status = {"rate": 429, "bad_request": 400, "schema_bad_request": 400, "denied": 403, "missing_model": 404}[self.fail]
                    raise HTTPError(url, status, "upstream error", {}, None)
            return Response({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": self.output.model_dump_json()}]}}]})
        if "upload/v1beta/files" in url and method == "POST":
            if self.fail == "upload_bad_request":
                raise HTTPError(url, 400, "upstream error", {}, None)
            return Response({}, {"X-Goog-Upload-URL": "https://generativelanguage.googleapis.com/upload/binary"})
        if url.endswith("/upload/binary"):
            self.counter += 1
            return Response({"file": {"name": f"files/file{self.counter}", "state": "PROCESSING"}})
        if method == "GET":
            return Response({"state": "FAILED" if self.fail == "processing" else "ACTIVE", "uri": url})
        raise AssertionError((method, url))


def analyzer_for(case, *, output=None, fail=None):
    snapshot = build_analysis_input(case)
    server = GeminiServer(output or output_for(snapshot), fail=fail)
    objects = {item.storage_path: data for item, data in zip(case.evidence, [b"photo", b"video"])}
    storage = SimpleNamespace(read_object=lambda path, limit: objects[path])
    analyzer = GeminiClaimsAnalyzer(storage=storage, api_key="test-key", open_url=server, sleep=lambda _: None)
    return analyzer, server, snapshot


def test_generation_schema_uses_only_supported_gemini_fields():
    schema = gemini_response_schema()

    def check(node):
        if isinstance(node, list):
            for value in node:
                check(value)
        elif isinstance(node, dict):
            assert set(node) <= GEMINI_SCHEMA_FIELDS
            for key, value in node.items():
                if key in {"$defs", "properties"}:
                    for child in value.values():
                        check(child)
                else:
                    check(value)

    check(schema)
    assert schema["properties"]["schema_version"]["enum"] == [1]
    assert "minLength" not in json.dumps(schema)
    assert "pattern" not in json.dumps(schema)


def test_joint_photo_and_cctv_request_persisted_with_no_automatic_draft():
    case = case_with_media()
    analyzer, server, snapshot = analyzer_for(case)
    repository = MemoryAnalysisRepository(case)
    result = AnalysisService(repository, analyzer).run(str(ACTOR), case.id, AnalysisRunRequest(expected_state_version=1))
    assert result.analysis_run.status == "ready"
    assert result.case.current_draft is None
    assert result.analysis_run.output.media_analysis.damages[0].estimate.maximum_minor == 100000
    assert result.analysis_run.output.amount is None
    assert result.analysis_run.output.proposed_route == "handler_review"
    assert result.analysis_run.gate_results[-1].status == "needs_review"
    parts = server.generation["contents"][0]["parts"]
    assert [part["fileData"]["mimeType"] for part in parts if "fileData" in part] == ["image/jpeg", "video/mp4"]
    media_parts = [part for part in parts if "fileData" in part]
    assert "videoMetadata" not in media_parts[0]
    assert media_parts[1]["videoMetadata"] == {"fps": 5}
    assert all(str(item.id) in str(parts) for item in snapshot.evidence)
    assert "storage_path" not in parts[0]["text"]
    assert len(server.deleted) == 2
    assert sum(":generateContent" in req.full_url for req in server.requests) == 1


@pytest.mark.parametrize("mutation, code", [
    ("foreign", "invalid_source_ref"), ("missing", "incomplete_media_analysis"),
    ("video_timestamp", "invalid_source_ref"), ("photo_timestamp", "invalid_source_ref"),
    ("liability", "unsupported_claim"), ("no_media", "incomplete_media_analysis"),
])
def test_invalid_or_incomplete_findings_fail_and_cleanup(mutation, code):
    case = case_with_media()
    output = output_for(build_analysis_input(case))
    if mutation == "foreign":
        output.media_analysis.observations[0].citations[0].evidence_id = uuid4()
    elif mutation == "missing":
        output.media_analysis.analyzed_evidence_ids.pop()
    elif mutation == "video_timestamp":
        output.media_analysis.observations[0].citations[0].timestamp_seconds = None
    elif mutation == "photo_timestamp":
        output.media_analysis.damages[0].citations[0].timestamp_seconds = 1
    elif mutation == "liability":
        output.media_analysis.liability.likely_responsible = "third_party"
    else:
        output.media_analysis = None
    analyzer, server, snapshot = analyzer_for(case, output=output)
    with pytest.raises(AnalysisError) as error:
        analyzer.analyze(snapshot)
    assert error.value.code == code
    assert len(server.deleted) == 2


@pytest.mark.parametrize("fail, code, count", [
    ("rate", "gemini_rate_limited", 2),
    ("bad_request", "gemini_invalid_request", 2),
    ("denied", "gemini_access_denied", 2),
    ("missing_model", "gemini_resource_not_found", 2),
    ("upload_bad_request", "gemini_invalid_request", 0),
    ("processing", "gemini_media_failed", 1),
])
def test_upstream_failures_cleanup(fail, code, count):
    analyzer, server, snapshot = analyzer_for(case_with_media(), fail=fail)
    with pytest.raises(AnalysisError) as error:
        analyzer.analyze(snapshot)
    assert error.value.code == code
    assert len(server.deleted) == count
    if fail == "upload_bad_request":
        assert "file upload" in str(error.value)
    elif fail in {"bad_request", "denied", "missing_model"}:
        assert "generation" in str(error.value)
        assert server.generation_calls == (2 if fail == "bad_request" else 1)


def test_generation_400_retries_without_api_schema_and_validates_output():
    analyzer, server, snapshot = analyzer_for(case_with_media(), fail="schema_bad_request")

    output = analyzer.analyze(snapshot)

    assert output.media_analysis is not None
    assert server.generation_calls == 2
    assert "responseJsonSchema" not in server.generation["generationConfig"]
    assert "JSON matching this output contract" in server.generation["contents"][0]["parts"][0]["text"]
    assert server.counter == 2
    assert len(server.deleted) == 2


def test_generation_503_retries_without_reuploading_media():
    analyzer, server, snapshot = analyzer_for(case_with_media(), fail="unavailable_once")

    output = analyzer.analyze(snapshot)

    assert output.media_analysis is not None
    assert server.generation_calls == 2
    assert server.counter == 2
    assert len(server.deleted) == 2


def test_persistent_generation_503_stops_after_three_attempts_and_cleans_up():
    analyzer, server, snapshot = analyzer_for(case_with_media(), fail="unavailable")

    with pytest.raises(AnalysisError) as error:
        analyzer.analyze(snapshot)

    assert error.value.code == "gemini_temporarily_unavailable"
    assert "temporairement indisponible" in str(error.value)
    assert server.generation_calls == 3
    assert server.counter == 2
    assert len(server.deleted) == 2


@pytest.mark.parametrize("mutation, code", [("checksum", "media_integrity_failed"), ("path", "invalid_media_path"), ("size", "media_limit_exceeded")])
def test_integrity_and_case_scope_before_upload(mutation, code):
    analyzer, server, snapshot = analyzer_for(case_with_media())
    if mutation == "checksum":
        snapshot.evidence[0].client_sha256 = "0" * 64
    elif mutation == "path":
        snapshot.evidence[0].storage_path = f"{uuid4()}/other"
    else:
        snapshot.evidence[0].byte_size = 51 * 1_048_576
    with pytest.raises(AnalysisError) as error:
        analyzer.analyze(snapshot)
    assert error.value.code == code
    assert not server.requests


def test_missing_key_no_external_request():
    analyzer, server, snapshot = analyzer_for(case_with_media())
    analyzer.api_key = ""
    with pytest.raises(AnalysisError, match="GEMINI_API_KEY"):
        analyzer.analyze(snapshot)
    assert not server.requests


def test_processing_timeout_cleans_up():
    analyzer, server, snapshot = analyzer_for(case_with_media())
    analyzer.timeout_seconds = 1
    with pytest.raises(AnalysisError) as error:
        analyzer.analyze(snapshot)
    assert error.value.code == "analysis_timeout"
    assert len(server.deleted) == 1


def test_damage_range_validation():
    with pytest.raises(ValidationError):
        DamageEstimate(minimum_minor=100, maximum_minor=50, currency="EUR", assumptions="test")


@pytest.mark.parametrize("mime, kind", [("video/mp4", "cctv_video"), ("video/quicktime", "insured_video"), ("video/webm", "cctv_video")])
def test_video_upload_supports_50_mib(mime, kind):
    repo = MemoryEvidenceRepository()
    service = EvidenceService(repo, FakeStorage())
    intent = service.create_upload_intent(str(ACTOR), repo.case.id, _upload_request(repo.case, mime_type=mime, kind=kind, byte_size=52_428_800))
    assert intent.byte_size == 52_428_800


@pytest.mark.parametrize("changes", [{"byte_size": 5_242_881}, {"mime_type": "video/mp4"}, {"kind": "cctv_video"}])
def test_upload_rejects_oversized_images_or_wrong_video_category(changes):
    repo = MemoryEvidenceRepository()
    with pytest.raises(InvalidEvidenceUploadError):
        EvidenceService(repo, FakeStorage()).create_upload_intent(str(ACTOR), repo.case.id, _upload_request(repo.case, **changes))


def test_storage_download_is_bounded():
    def open_url(request, timeout):
        assert "/object/authenticated/" in request.full_url
        return BytesIO(b"oversized")
    storage = SupabaseStorageAdapter("https://project.example", "test-secret", open_url=open_url)
    with pytest.raises(StorageAdapterError, match="size"):
        storage.read_object("case/file", 3)


def test_video_migration_renders_without_database(monkeypatch):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://unused:unused@localhost/claim_test?sslmode=require")
    output = StringIO()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "20260925_analysis_inputs:20260926_video_evidence", sql=True)
    assert "52428800" in output.getvalue()
    assert "video_kind_check" in output.getvalue()


def test_schema_fallback_does_not_consume_transient_retry_budget():
    analyzer, server, snapshot = analyzer_for(case_with_media())
    original = server.__call__
    calls = []
    def flaky(request, timeout):
        if ':generateContent' in request.full_url:
            calls.append(json.loads(request.data))
            if len(calls) <= 3:
                status = 400 if len(calls) == 1 else 503
                raise HTTPError(request.full_url, status, 'temporary error', {}, None)
        return original(request, timeout)
    analyzer._open_url = flaky
    output = analyzer.analyze(snapshot)
    assert output.media_analysis
    assert len(calls) == 4
    assert 'responseJsonSchema' not in calls[-1]['generationConfig']


def test_visual_only_contract_uses_media_schema_and_never_creates_claim_amount():
    case = case_with_media()
    analyzer, server, snapshot = analyzer_for(case)
    server.output = server.output.media_analysis
    analyzer.media_only = True
    output = analyzer.analyze(snapshot)
    assert output.media_analysis and output.amount is None and output.recipient is None
    assert output.proposed_route == "handler_review"
    schema = server.generation["generationConfig"]["responseJsonSchema"]
    assert "analyzed_evidence_ids" in schema["properties"]
    assert "propositions" not in schema["properties"]


def test_rejected_visual_schema_preserves_structured_findings_and_original_media():
    analyzer, server, snapshot = analyzer_for(case_with_media(), fail="schema_bad_request")
    analyzer.media_only = True
    server.output = server.output.media_analysis
    output = analyzer.analyze(snapshot)
    assert output.media_analysis.plates and output.media_analysis.damages
    assert output.media_analysis.damages[0].estimate.minimum_minor == 50000
    assert output.amount is None and output.recipient is None
    body = server.generation
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseJsonSchema" not in body["generationConfig"]
    assert "vehicle_assessments" in body["contents"][0]["parts"][0]["text"]
    assert len([p for p in body["contents"][0]["parts"] if "fileData" in p]) == 2
    assert server.generation_calls == 2 and len(server.deleted) == 2


def test_plain_text_fallback_is_a_failure_not_a_success_with_empty_findings():
    analyzer, server, snapshot = analyzer_for(case_with_media())
    analyzer.media_only = True
    original = server.__call__
    generations = []
    def transport(request, timeout):
        if ':generateContent' in request.full_url:
            generations.append(json.loads(request.data))
            if len(generations) == 1:
                raise HTTPError(request.full_url, 400, 'schema rejected', {}, None)
            return Response({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'Un véhicule tourne.'}]}}]})
        return original(request, timeout)
    analyzer._open_url = transport
    with pytest.raises(AnalysisError) as error:
        analyzer.analyze(snapshot)
    assert error.value.code == 'gemini_invalid_response'
    assert len(server.deleted) == 2


def test_per_vehicle_responsibility_requires_citations_from_this_case():
    from claim_api.models import VehicleLiabilityAssessment
    analyzer, server, snapshot = analyzer_for(case_with_media())
    assessment = VehicleLiabilityAssessment(vehicle="A", role="insured", assessment="undetermined",
        reasoning="Approche hors champ", confidence="low",
        citations=[{"evidence_id": snapshot.evidence[1].id, "timestamp_seconds": 2.5}])
    server.output.media_analysis.liability.vehicle_assessments = [assessment]
    assert analyzer.analyze(snapshot).media_analysis.liability.vehicle_assessments[0].vehicle == "A"
    assessment.citations[0].evidence_id = uuid4()
    with pytest.raises(AnalysisError) as error:
        analyzer.analyze(snapshot)
    assert error.value.code == "invalid_source_ref"


def test_missing_vehicle_assessment_does_not_become_a_ready_report():
    analyzer, server, snapshot = analyzer_for(case_with_media())
    server.output.media_analysis.liability.vehicle_assessments = []
    with pytest.raises(AnalysisError) as error:
        analyzer.analyze(snapshot)
    assert error.value.code == "incomplete_media_analysis"
