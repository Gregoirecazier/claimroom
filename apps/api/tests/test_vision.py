from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from test_video_reuse import MemoryRepository as MemoryArtifactRepository

from claim_api.models import EvidenceView
from claim_api.video_reuse import (
    VideoAnalysisResponse, VideoObservation, VideoObservationSource,
    VideoPipelineSpec,
)
from claim_api.vision import (
    ImageZone, VisionRequest, VisionService, VisualFact, mp4_duration_ms,
    source_fact, validate_facts, validate_video_observations,
)


ACTOR = uuid4()
CASE = uuid4()
PHOTO = b"synthetic photo bytes"


class MemoryRepository:
    def __init__(self, evidence, actor=ACTOR):
        self.actor = actor
        self.evidence = evidence
        self.saved = []
        self.artifacts = MemoryArtifactRepository()
        self.artifacts.evidence[(actor, evidence.case_id, evidence.id)] = evidence

    def owned_evidence(self, actor, case_id, evidence_id):
        return self.evidence if (actor, case_id, evidence_id) == (self.actor, self.evidence.case_id, self.evidence.id) else None

    def save(self, actor, case_id, evidence_id, mode, status, fingerprint, facts, reason):
        assert (actor, case_id, evidence_id) == (self.actor, self.evidence.case_id, self.evidence.id)
        self.saved.append((mode, status, fingerprint, facts, reason))
        return uuid4()


class MemoryStorage:
    def __init__(self, data=PHOTO, unavailable=False):
        self.data, self.unavailable, self.reads = data, unavailable, 0

    def read_object_chunks(self, _path):
        self.reads += 1
        if self.unavailable:
            from claim_api.storage import StorageUnavailableError
            raise StorageUnavailableError("private object unavailable")
        yield self.data


class FakeAnalyzer:
    mode = "mock"

    def __init__(self, facts, expected=PHOTO):
        self.facts, self.expected, self.calls = facts, expected, 0

    def analyze_image(self, path: Path, mime_type: str, spec: VideoPipelineSpec):
        self.calls += 1
        assert path.read_bytes() == self.expected
        assert mime_type == "image/png"
        return self.facts


def evidence(mime="image/png", data=PHOTO):
    return EvidenceView(id=uuid4(), case_id=CASE, kind="vehicle_photo", storage_path="private/object",
        source_kind="handler_upload", mode="mock", mime_type=mime, byte_size=len(data),
        client_sha256=hashlib.sha256(data).hexdigest(), checksum_status="verified",
        sha256_verified=hashlib.sha256(data).hexdigest(), received_at=datetime.now(timezone.utc))


def pipeline():
    return VideoPipelineSpec(analyzer_name="fake", model_id="fake", prompt_version="test",
        output_schema_version=2, frame_sampling="1fps", resize_policy="original",
        ocr_version="none", preprocessing_version="test")


def fact(**changes):
    values = dict(category="damage", status="observed", text="Visible deformation on a silver car.",
                  zone=ImageZone(x=.1, y=.2, width=.4, height=.3), vehicle_track_id="car-1")
    values.update(changes)
    return VisualFact(**values)


def test_photo_requires_opt_in_and_records_case_local_source():
    item = evidence()
    repo, storage = MemoryRepository(item), MemoryStorage()
    analyzer = FakeAnalyzer([fact(), fact(category="plate", status="uncertain",
        text="Several characters of a plate are uncertain.", plate_candidate="RK18 LX?",
        uncertain_positions=[7], confidence=.45)])
    service = VisionService(repo, storage, analyzer=analyzer, pipeline=pipeline())
    miss = service.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id))
    assert miss.status == "not_preprocessed" and analyzer.calls == storage.reads == 0
    result = service.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id, processing_policy="allow_new"))
    assert result.status == "uncertain" and result.mode == "mock"
    assert analyzer.calls == storage.reads == 1
    assert result.observations[0].media_id == item.id
    assert result.observations[0].source_ref.id == str(item.id)
    assert result.observations[0].source_ref.locator == "image:0.1000,0.2000,0.4000,0.3000"
    assert result.observations[1].plate_candidate == "RK18 LX?"
    assert result.observations[1].uncertain_positions == [7]
    assert len(repo.saved) == 1 and repo.saved[0][1] == "uncertain"


def test_two_g1_photo_fixtures_get_distinct_case_local_sources():
    base = Path(__file__).resolve().parents[1] / "claim_api/fixture_media/g1"
    ids = set()
    for name in ("photo-ensemble.png", "photo-detail.png"):
        data = (base / name).read_bytes()
        item = evidence(data=data)
        ids.add(item.id)
        service = VisionService(MemoryRepository(item), MemoryStorage(data),
            analyzer=FakeAnalyzer([fact()], expected=data), pipeline=pipeline())
        result = service.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id,
                                                             processing_policy="allow_new"))
        assert result.status == "observed"
        assert result.observations[0].media_id == item.id
        assert result.observations[0].source_ref.id == str(item.id)
    assert len(ids) == 2


def test_image_zone_plate_and_media_type_are_validated():
    with pytest.raises(ValidationError):
        ImageZone(x=.9, y=0, width=.2, height=.2)
    with pytest.raises(ValidationError):
        fact(category="plate", plate_candidate="O?", uncertain_positions=[])
    with pytest.raises(ValueError, match="image zone"):
        validate_facts(uuid4(), "image/png", [VisualFact(category="movement", status="observed",
            text="Moving", start_ms=0, end_ms=10)])
    # No insured/third-party role exists in the intrinsic fact contract.
    with pytest.raises(ValidationError):
        VisualFact.model_validate({**fact().model_dump(), "party_role": "third_party"})


def test_empty_or_inaccessible_image_is_error_or_unavailable_not_no_findings():
    item = evidence()
    repo = MemoryRepository(item)
    empty = VisionService(repo, MemoryStorage(), FakeAnalyzer([]), pipeline=pipeline())
    result = empty.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id, processing_policy="allow_new"))
    assert result.status == "error" and result.observations == []
    blocked = VisionService(repo, MemoryStorage(unavailable=True), FakeAnalyzer([fact()]), pipeline=pipeline())
    result = blocked.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id, processing_policy="allow_new"))
    assert result.status == "unavailable" and result.observations == []
    assert [entry[1] for entry in repo.saved] == ["error", "unavailable"]


def test_video_reuse_only_never_calls_analyzer_and_rebinds_case_id():
    item = evidence("video/mp4")
    repo = MemoryRepository(item)

    class FakeVideo:
        def __init__(self): self.calls = []
        def run(self, actor, case, evidence_id, request):
            self.calls.append(request.processing_policy)
            if request.processing_policy == "reuse_only":
                return VideoAnalysisResponse(status="not_preprocessed", pipeline_fingerprint="f" * 64,
                    analysis_reused=False)
            observation = VideoObservation(start_ms=100, end_ms=400, category="vehicle",
                description="A blue car crosses the frame.", vehicle_track_id="blue-1")
            return VideoAnalysisResponse(status="reused", pipeline_fingerprint="f" * 64,
                analysis_reused=True, observations=[VideoObservationSource(observation=observation,
                source_ref={"kind": "evidence", "id": str(evidence_id), "locator": "video:100-400"})])

    video = FakeVideo()
    service = VisionService(repo, MemoryStorage(), video=video, pipeline=pipeline())
    first = service.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id))
    second = service.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id, processing_policy="allow_new"))
    assert first.status == "not_preprocessed" and repo.saved[0][1] == "observed"
    assert second.analysis_reused and second.observations[0].source_ref.id == str(item.id)
    assert second.observations[0].vehicle_track_id == "blue-1"
    assert video.calls == ["reuse_only", "allow_new"]


def test_fixture_mp4_duration_bounds_and_no_invented_after_clip_timecode():
    path = Path(__file__).resolve().parents[1] / "claim_api/fixture_media/g1/video-g1.mp4"
    assert 5000 <= mp4_duration_ms(path) <= 5100
    validate_video_observations(path, [VideoObservation(start_ms=4500, end_ms=5000,
        category="movement", description="A vehicle is visible near the end of the clip.")])
    with pytest.raises(ValueError, match="exceeds verified duration"):
        validate_video_observations(path, [VideoObservation(start_ms=5000, end_ms=6000,
            category="movement", description="Invented later movement.")])
    with pytest.raises(ValueError, match="empty video result"):
        validate_video_observations(path, [])


def test_vision_provider_result_is_persisted_and_read_case_locally(monkeypatch):
    import os
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from claim_api.case_service import CaseService
    from claim_api.postgres_cases import PostgresCaseRepository
    from claim_api.vision import PostgresVisionRepository

    url = os.getenv("TEST_MIGRATION_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_MIGRATION_DATABASE_URL to a disposable loopback PostgreSQL test database")
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1", "::1"} or "test" not in (parsed.database or "").lower():
        pytest.fail("TEST_MIGRATION_DATABASE_URL must target a loopback test database")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_ALLOW_INSECURE_LOCAL", "true")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", url)
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text("create schema if not exists auth"))
        connection.execute(text("create table if not exists auth.users (id uuid primary key)"))
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
    actor = uuid4()
    with engine.begin() as connection:
        connection.execute(text("insert into auth.users (id) values (:id)"), {"id": str(actor)})
    cases = PostgresCaseRepository(url)
    case = CaseService(cases).create_case(str(actor), "g1")
    image_id = uuid4()
    with engine.begin() as connection:
        connection.execute(text("""insert into public.evidence
            (id,case_id,storage_path,kind,source_kind,mode,mime_type,byte_size,
             client_sha256,checksum_status,sha256_verified)
            values (:id,:case_id,:path,'vehicle_photo','test_fixture','mock','image/png',
                    :size,:sha,'verified',:sha)"""), {
            "id": str(image_id), "case_id": str(case.id), "path": f"{case.id}/test.png",
            "size": len(PHOTO), "sha": hashlib.sha256(PHOTO).hexdigest(),
        })
    repository = PostgresVisionRepository(cases)
    source = source_fact(image_id, fact())
    first_id = repository.save(actor, case.id, image_id, "mock", "observed",
                               pipeline().fingerprint, [source], None)
    persisted = cases.get_case(actor, case.id)
    assert persisted and persisted.content_revision == case.content_revision + 1
    result = next(item for item in persisted.provider_results if item.id == first_id)
    assert result.provider == "vision" and result.mode == "mock" and result.status == "matched"
    assert result.data["observations"][0]["source_ref"]["id"] == str(image_id)
    assert repository.save(actor, case.id, image_id, "mock", "observed",
                           pipeline().fingerprint, [source], None) == first_id
    assert cases.get_case(actor, case.id).content_revision == persisted.content_revision

    analyzer = FakeAnalyzer([fact()])
    request = VisionRequest(evidence_id=image_id, processing_policy="allow_new")
    first = VisionService(repository, MemoryStorage(), analyzer, pipeline=pipeline()).run(str(actor), case.id, request)
    # Recreate both service and repository: persistence, not a process-local cache.
    replay = VisionService(PostgresVisionRepository(cases), MemoryStorage(unavailable=True),
                           analyzer, pipeline=pipeline()).run(str(actor), case.id, request)
    assert first.status == replay.status == "observed"
    assert not first.analysis_reused and replay.analysis_reused
    assert first.provider_result_id == replay.provider_result_id == first_id
    assert analyzer.calls == 1
    assert cases.get_case(actor, case.id).content_revision == persisted.content_revision
    with engine.connect() as connection:
        stored = connection.execute(text("""select observations_json from public.media_analysis_artifacts
            where scope=:scope and sha256_verified=:sha"""), {
            "scope": f"user:{actor}:image:image/png", "sha": hashlib.sha256(PHOTO).hexdigest(),
        }).scalar_one()
    assert "media_id" not in stored[0] and "source_ref" not in stored[0]
    engine.dispose()


def test_analysis_uses_only_valid_matched_case_local_visual_sources():
    from claim_api.analysis import build_analysis_input
    from claim_api.fixtures import get_scenario
    from claim_api.models import CaseView, ProviderResultView
    from claim_api.video_reuse import configured_pipeline

    item = evidence()
    fingerprint = configured_pipeline().model_copy(update={"output_schema_version": 2}).fingerprint
    source = source_fact(item.id, fact())
    now = datetime.now(timezone.utc)

    def provider(status, facts):
        return ProviderResultView(id=uuid4(), source_id=uuid4(), provider="vision", mode="mock",
            status=status, source_version="vision-observation-v1", query_hash="f" * 64,
            retrieved_at=now, data={"evidence_id": str(item.id), "pipeline_fingerprint": fingerprint,
                                    "observations": facts})

    good = provider("matched", [source.model_dump(mode="json")])
    bad = provider("error", [source.model_dump(mode="json")])
    foreign = source_fact(uuid4(), fact())
    fake_foreign = provider("matched", [foreign.model_dump(mode="json")])
    case = CaseView(id=CASE, created_by_user_id=ACTOR, scenario_id="g1", synthetic=True,
        status="collecting", state_version=1, content_revision=1, created_at=now, updated_at=now,
        intake=get_scenario("g1").intake, evidence=[item], provider_results=[good, bad, fake_foreign])
    snapshot = build_analysis_input(case)
    visual_excerpts = [entry for entry in snapshot.source_excerpts if entry.source_ref.locator.startswith("image:")]
    assert len(visual_excerpts) == 1
    assert visual_excerpts[0].source_ref.id == str(item.id)
    assert "Visible deformation" in visual_excerpts[0].text


def test_opt_in_mock_accepts_exact_g1_g2_g3_media_without_party_labels():
    from claim_api.fixture_vision import FixtureMockAnalyzer, UnsupportedFixtureError

    adapter = FixtureMockAnalyzer()
    root = Path(__file__).resolve().parents[1] / "claim_api/fixture_media"
    for group, photo_names in {
        "g1": ("photo-ensemble.png", "photo-detail.png"),
        "g2": ("photo-renault-ensemble.png", "photo-renault-detail.png"),
        "g3": ("photo-toyota-ensemble.png", "photo-toyota-detail.png"),
    }.items():
        for name in photo_names:
            facts = adapter.analyze_image(root / group / name, "image/png", pipeline())
            assert facts and all(item.zone and item.start_ms is None for item in facts)
            assert all("party_role" not in item.model_dump() for item in facts)
        video = next((root / group).glob("video*.mp4"))
        observations, usage = adapter.analyze(video, pipeline())
        validate_video_observations(video, observations)
        assert usage["cost_minor"] == 0
        assert all(item.vehicle_track_id for item in observations)
        if group == "g2":
            blue = next(item for item in observations if item.plate_candidate == "XY34 ZTR")
            dark = next(item for item in observations if item.plate_candidate == "RK18 L?P")
            assert blue.vehicle_track_id != dark.vehicle_track_id
            assert dark.uncertain_positions == [6]
        if group == "g3":
            assert any(item.category == "movement" and "light-coloured car moves" in item.description
                       for item in observations)
            assert all("fraud" not in item.description.lower() for item in observations)
    unknown = Path(__file__)
    with pytest.raises(UnsupportedFixtureError):
        adapter.analyze_image(unknown, "image/png", pipeline())


def test_vision_route_exposes_explicit_processing_status_without_credentials():
    from fastapi.testclient import TestClient
    from claim_api.auth import AuthenticatedUser, get_current_user
    from claim_api.main import create_app
    from claim_api.routes.vision import get_vision_service

    item = evidence()
    service = VisionService(MemoryRepository(item), MemoryStorage(),
        analyzer=FakeAnalyzer([fact()]), pipeline=pipeline())
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(ACTOR))
    app.dependency_overrides[get_vision_service] = lambda: service
    client = TestClient(app)
    miss = client.post(f"/v1/cases/{CASE}/vision-observations", json={"evidence_id": str(item.id)})
    assert miss.status_code == 200 and miss.json()["status"] == "not_preprocessed"
    prepared = client.post(f"/v1/cases/{CASE}/vision-observations", json={
        "evidence_id": str(item.id), "processing_policy": "allow_new"})
    assert prepared.status_code == 200
    assert prepared.json()["observations"][0]["source_ref"]["id"] == str(item.id)
    foreign = client.post(f"/v1/cases/{uuid4()}/vision-observations", json={"evidence_id": str(item.id)})
    assert foreign.status_code == 404


def test_photo_cache_survives_service_recreation_and_rebinds_another_case():
    item = evidence()
    repo, storage, analyzer = MemoryRepository(item), MemoryStorage(), FakeAnalyzer([fact()])
    request = VisionRequest(evidence_id=item.id, processing_policy="allow_new")
    service = VisionService(repo, storage, analyzer, pipeline=pipeline())
    assert not service.run(str(ACTOR), CASE, request).analysis_reused
    assert service.run(str(ACTOR), CASE, request).analysis_reused
    copy = evidence().model_copy(update={"case_id": uuid4()})
    next_repo = MemoryRepository(copy)
    next_repo.artifacts = repo.artifacts
    restarted = VisionService(next_repo, MemoryStorage(unavailable=True), analyzer, pipeline=pipeline())
    hit = restarted.run(str(ACTOR), copy.case_id, VisionRequest(evidence_id=copy.id))
    assert hit.analysis_reused and hit.observations[0].media_id == copy.id
    assert hit.observations[0].source_ref.id == str(copy.id)
    assert analyzer.calls == storage.reads == 1
    stored = next(iter(repo.artifacts.artifacts.values()))["observations_json"]
    assert "media_id" not in stored[0] and "source_ref" not in stored[0]


@pytest.mark.parametrize("change", [
    {"prompt_version": "next"}, {"model_id": "next"}, {"output_schema_version": 3},
    {"resize_policy": "small"}, {"deterministic_parameters": {"temperature": 1}},
])
def test_photo_pipeline_change_requires_new_analysis(change):
    item = evidence()
    repo, storage, analyzer = MemoryRepository(item), MemoryStorage(), FakeAnalyzer([fact()])
    request = VisionRequest(evidence_id=item.id, processing_policy="allow_new")
    VisionService(repo, storage, analyzer, pipeline=pipeline()).run(str(ACTOR), CASE, request)
    changed = VisionService(repo, storage, analyzer, pipeline=pipeline().model_copy(update=change))
    assert changed.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id)).status == "not_preprocessed"
    assert analyzer.calls == 1
    assert changed.run(str(ACTOR), CASE, request).status == "observed"
    assert analyzer.calls == 2


def test_photo_cache_is_scoped_and_client_hash_alone_cannot_hit():
    item = evidence()
    repo, storage, analyzer = MemoryRepository(item), MemoryStorage(), FakeAnalyzer([fact()])
    service = VisionService(repo, storage, analyzer, pipeline=pipeline())
    service.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id, processing_policy="allow_new"))
    other_actor = uuid4()
    other = MemoryRepository(item, actor=other_actor)
    other.artifacts = repo.artifacts
    assert VisionService(other, storage, analyzer, pipeline=pipeline()).run(
        str(other_actor), CASE, VisionRequest(evidence_id=item.id)).status == "not_preprocessed"
    changed = evidence(data=PHOTO + b"!")
    other = MemoryRepository(changed)
    other.artifacts = repo.artifacts
    assert VisionService(other, storage, analyzer, pipeline=pipeline()).run(
        str(ACTOR), CASE, VisionRequest(evidence_id=changed.id)).status == "not_preprocessed"
    unverified = item.model_copy(update={"checksum_status": "client_declared", "sha256_verified": None})
    repo.evidence = unverified
    result = VisionService(repo, MemoryStorage(PHOTO[:-1] + b"!"), analyzer, pipeline=pipeline()).run(
        str(ACTOR), CASE, VisionRequest(evidence_id=item.id, processing_policy="allow_new"))
    assert result.status == "error" and not result.analysis_reused and analyzer.calls == 1
    repo.artifacts.evidence[(ACTOR, CASE, item.id)] = unverified
    assert service.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id)).analysis_reused
    assert analyzer.calls == 1


def test_concurrent_photo_requests_call_analyzer_once():
    entered, release = threading.Event(), threading.Event()
    item = evidence()
    repo, storage = MemoryRepository(item), MemoryStorage()

    class SlowAnalyzer(FakeAnalyzer):
        def analyze_image(self, path, mime_type, spec):
            facts = super().analyze_image(path, mime_type, spec)
            entered.set()
            assert release.wait(timeout=5)
            return facts

    analyzer = SlowAnalyzer([fact()])
    service = VisionService(repo, storage, analyzer, pipeline=pipeline())
    request = VisionRequest(evidence_id=item.id, processing_policy="allow_new")
    results = []
    worker = threading.Thread(target=lambda: results.append(service.run(str(ACTOR), CASE, request)))
    worker.start()
    try:
        assert entered.wait(timeout=5)
        assert service.run(str(ACTOR), CASE, request).status == "processing"
        assert analyzer.calls == 1
    finally:
        release.set()
        worker.join(timeout=5)
    assert results[0].status == "observed"
    assert service.run(str(ACTOR), CASE, request).analysis_reused
    assert analyzer.calls == 1


def test_failed_photo_is_not_cached_and_retry_can_succeed():
    item = evidence()
    repo, storage, analyzer = MemoryRepository(item), MemoryStorage(), FakeAnalyzer([])
    service = VisionService(repo, storage, analyzer, pipeline=pipeline())
    request = VisionRequest(evidence_id=item.id, processing_policy="allow_new")
    assert service.run(str(ACTOR), CASE, request).status == "error"
    assert service.run(str(ACTOR), CASE, VisionRequest(evidence_id=item.id)).status == "not_preprocessed"
    analyzer.facts = [fact()]
    assert service.run(str(ACTOR), CASE, request).status == "observed"
    assert service.run(str(ACTOR), CASE, request).analysis_reused
    assert analyzer.calls == 2
