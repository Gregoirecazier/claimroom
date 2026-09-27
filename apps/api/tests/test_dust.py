from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
from pathlib import Path
from urllib.error import HTTPError
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest

from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.dust_client import DEFAULT_AGENTS, DustClient, DustError, NoRedirects
from claim_api.dust_models import DustRunRequest
from claim_api.dust_repository import PostgresDustRepository
from claim_api.dust_service import (
    CAMERCI_URL,
    DustService,
    build_dust_input,
    make_prompt,
    parse_report,
)
from claim_api.main import create_app
from claim_api.models import AnalysisRunView
from claim_api.routes.dust import get_dust_service
from test_analysis import ACTOR, make_case
from test_gemini_analysis import case_with_media, output_for
from claim_api.analysis import build_analysis_input


def media_case():
    case = case_with_media().model_copy(deep=True)
    case.latest_analysis = AnalysisRunView(
        id=uuid4(),
        input_content_revision=case.content_revision,
        method_version="gemini-joint-media-v1:test",
        status="ready",
        mode="live",
        output=output_for(build_analysis_input(case)),
        started_at=case.created_at,
    )
    return case


def request_for(case, role="repair", **kwargs):
    return DustRunRequest(
        agent=role,
        expected_state_version=case.state_version,
        idempotency_key=uuid4(),
        **kwargs,
    )


def report_for(snapshot):
    return {
        "case_id": snapshot["case_id"],
        "content_revision": snapshot["content_revision"],
        "agent": snapshot["agent"],
        "mode": "demo",
        "status": "needs_information",
        "summary": "Devis nécessaire.",
        "findings": [],
        "sources": [],
        "missing_information": ["Devis du réparateur"],
        "proposed_actions": ["Demander un devis"],
        "human_review_required": True,
    }


class Response(BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


def client_with(open_url):
    return DustClient(
        api_key="test-secret",
        workspace_id="test-workspace",
        base_url="https://dust.tt",
        agents=DEFAULT_AGENTS.copy(),
        open_url=open_url,
    )


def test_repair_receives_gemini_findings_and_exact_citations_but_no_files():
    case = media_case()
    snapshot = build_dust_input(case, request_for(case), [])
    assert (
        snapshot["gemini_media_analysis"]["observations"][0]["citations"][0][
            "timestamp_seconds"
        ]
        == 2.5
    )
    assert snapshot["gemini_analysis_run_id"] == str(case.latest_analysis.id)
    prompt = make_prompt(snapshot)
    assert "Tu n'as pas reçu ni visionné les vidéos" in prompt
    assert "storage_path" not in prompt and "fileData" not in prompt
    for evidence in case.evidence:
        assert evidence.storage_path not in prompt
        assert evidence.client_sha256 not in prompt


@pytest.mark.parametrize("change", ["missing", "old", "wrong_provider", "failed"])
def test_repair_requires_current_successful_gemini(change):
    case = media_case()
    if change == "missing":
        case.latest_analysis = None
    elif change == "old":
        case.content_revision += 1
    elif change == "wrong_provider":
        case.latest_analysis.method_version = "pipelex"
    else:
        case.latest_analysis.status = "failed"
    with pytest.raises(DustError, match="dust_current_gemini_analysis_required"):
        build_dust_input(case, request_for(case), [])


def test_cctv_uses_camerci_and_does_not_require_or_send_media():
    case = media_case()
    snapshot = build_dust_input(case, request_for(case, "cctv"), [])
    assert CAMERCI_URL in make_prompt(snapshot)
    assert snapshot["gemini_media_analysis"] is None
    assert snapshot["provider_results"] == []
    assert "insured_reference" not in snapshot["intake"]
    case.intake.location = None
    with pytest.raises(DustError, match="dust_cctv_location_time_required"):
        build_dust_input(case, request_for(case, "cctv"), [])


def test_gemini_citations_cannot_be_invented_or_shifted():
    case = media_case()
    snapshot = build_dust_input(case, request_for(case), [])
    payload = report_for(snapshot)
    citation = snapshot["gemini_media_analysis"]["observations"][0]["citations"][
        0
    ].copy()
    payload["sources"] = [
        {"id": "v1", "title": "Observation Gemini", "media_citation": citation}
    ]
    payload["findings"] = [
        {
            "text": "Collision rapportée par Gemini",
            "assessment": "supported",
            "source_ids": ["v1"],
        }
    ]
    assert (
        parse_report(json.dumps(payload), snapshot)
        .sources[0]
        .media_citation.timestamp_seconds
        == 2.5
    )
    citation["timestamp_seconds"] = 3.0
    with pytest.raises(DustError, match="dust_invalid_report"):
        parse_report(json.dumps(payload), snapshot)


@pytest.mark.parametrize(
    "change", ["case", "revision", "agent", "unsupported", "approval", "input_source"]
)
def test_rejects_invalid_or_unbound_reports(change):
    case = media_case()
    snapshot = build_dust_input(case, request_for(case), [])
    payload = report_for(snapshot)
    if change == "case":
        payload["case_id"] = str(uuid4())
    if change == "revision":
        payload["content_revision"] += 1
    if change == "agent":
        payload["agent"] = "legal"
    if change == "unsupported":
        payload["findings"] = [
            {"text": "Montant validé", "assessment": "supported", "source_ids": []}
        ]
    if change == "approval":
        payload["human_review_required"] = False
    if change == "input_source":
        payload["sources"] = [
            {"id": "p", "title": "Devis", "input_reference": "document:missing"}
        ]
    with pytest.raises(DustError, match="dust_invalid_report"):
        parse_report(json.dumps(payload), snapshot)


def test_dust_create_nonblocking_selects_agent_without_skipping_validation():
    requests = []

    def send(req, timeout):
        requests.append(req)
        return Response({"conversation": {"sId": "conversation1"}})

    client = client_with(send)
    assert client.create("repair", "Test", "Input") == "conversation1"
    req = requests[0]
    assert (
        req.full_url
        == "https://dust.tt/api/v1/w/test-workspace/assistant/conversations"
    )
    assert req.get_header("Authorization") == "Bearer test-secret"
    payload = json.loads(req.data)
    assert payload["blocking"] is False and payload["skipToolsValidation"] is False
    assert payload["message"]["mentions"] == [
        {"configurationId": DEFAULT_AGENTS["repair"]}
    ]


def test_config_rejects_arbitrary_host_and_redirects():
    client = client_with(lambda *a: pytest.fail("No network expected"))
    client.base_url = "https://attacker.invalid"
    with pytest.raises(DustError, match="dust_not_configured"):
        client.create("legal", "Test", "Test")
    assert (
        NoRedirects().redirect_request(
            None, None, 302, None, None, "https://attacker.invalid"
        )
        is None
    )


@pytest.mark.parametrize(
    "http_code,uncertain", [(401, False), (403, False), (429, False), (500, True)]
)
def test_creation_errors_are_sanitized_and_never_retried(http_code, uncertain):
    calls = []

    def fail(req, timeout):
        calls.append(req)
        raise HTTPError(req.full_url, http_code, "secret echoed by upstream", {}, None)

    with pytest.raises(DustError) as error:
        client_with(fail).create("legal", "T", "T")
    assert error.value.uncertain is uncertain
    assert "secret" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,expected",
    [
        ("succeeded", "ready"),
        ("created", "running"),
        ("gracefully_stopped", "ready"),
        ("interrupted", "failed"),
        ("failed", "failed"),
        ("cancelled", "failed"),
    ],
)
def test_reads_only_target_agent_public_answer(status, expected):
    def get(req, timeout):
        return Response(
            {
                "conversation": {
                    "sId": "c1",
                    "content": [
                        [
                            {
                                "type": "agent_message",
                                "configuration": {"sId": "other"},
                                "status": "succeeded",
                                "content": "wrong",
                            },
                            {
                                "type": "agent_message",
                                "configuration": {"sId": DEFAULT_AGENTS["repair"]},
                                "status": status,
                                "content": "answer",
                                "chainOfThought": "private reasoning",
                            },
                        ]
                    ],
                }
            }
        )

    actual, content = client_with(get).read("c1", DEFAULT_AGENTS["repair"])
    assert actual == expected
    assert content == ("answer" if expected == "ready" else None)


@pytest.mark.parametrize(
    "pause",
    [
        {"creditSpendCheckpointStatus": "paused"},
        {"actions": [{"status": "blocked_validation_required"}]},
        {"actions": [{"status": "blocked_user_answer_required"}]},
    ],
)
def test_dust_user_intervention_is_not_automatically_approved(pause):
    def get(req, timeout):
        assert req.method == "GET"
        return Response(
            {
                "conversation": {
                    "sId": "c1",
                    "content": [
                        [
                            {
                                "type": "agent_message",
                                "configuration": {"sId": DEFAULT_AGENTS["legal"]},
                                "status": "created",
                                **pause,
                            }
                        ]
                    ],
                }
            }
        )

    assert client_with(get).read("c1", DEFAULT_AGENTS["legal"]) == (
        "needs_action",
        None,
    )


@pytest.mark.parametrize(
    "response", [{}, {"conversation": None}, {"conversation": {"sId": "../escape"}}]
)
def test_malformed_create_response_remains_uncertain(response):
    with pytest.raises(DustError) as error:
        client_with(lambda req, timeout: Response(response)).create(
            "legal", "Test", "Test"
        )
    assert error.value.uncertain is True


def test_idempotency_key_cannot_be_reused_for_another_role():
    case = media_case()
    service, repo, calls = service_for(case)
    request = request_for(case)
    service.start(str(ACTOR), case.id, request)
    request.agent = "legal"
    with pytest.raises(DustError, match="dust_idempotency_conflict"):
        service.start(str(ACTOR), case.id, request)
    assert len(calls) == 1


def test_previous_reports_are_scoped_to_current_revision_and_not_human_approved():
    case = media_case()
    service, repo, _ = service_for(case)
    run = service.start(str(ACTOR), case.id, request_for(case, "legal"))
    repo.rows[run.id]["updated_at"] -= timedelta(seconds=4)
    ready = service.refresh(str(ACTOR), case.id, run.id)
    snapshot = build_dust_input(case, request_for(case, "recovery"), [ready])
    assert snapshot["previous_agent_reports"]["legal"]["human_validated"] is False
    case.content_revision += 1
    assert (
        build_dust_input(case, request_for(case, "recovery"), [ready])[
            "previous_agent_reports"
        ]
        == {}
    )


class MemoryDustRepository:
    view = staticmethod(PostgresDustRepository.view)

    def __init__(self, case):
        self.case, self.rows = case, {}

    def get_case(self, actor, cid):
        return self.case if actor == ACTOR and cid == self.case.id else None

    def find_request(self, actor, cid, key):
        return next((r for r in self.rows.values() if r["key"] == key), None)

    def list_runs(self, actor, cid):
        return [
            self.view(r, self.case.content_revision)
            for r in reversed(list(self.rows.values()))
        ]

    def get_run(self, actor, cid, rid):
        return self.rows.get(rid)

    def reserve(self, actor, case, request, digest, snapshot, client):
        now = datetime.now(timezone.utc)
        row = dict(
            id=uuid4(),
            case_id=case.id,
            agent=request.agent,
            agent_id=client.agents[request.agent],
            workspace_id=client.workspace_id,
            base_url=client.base_url,
            input_content_revision=case.content_revision,
            key=request.idempotency_key,
            request_hash=digest,
            input_json=snapshot,
            gemini_analysis_run_id=snapshot["gemini_analysis_run_id"],
            status="submitting",
            created_at=now,
            updated_at=now,
        )
        self.rows[row["id"]] = row
        return row, True

    def update_run(self, actor, cid, rid, **values):
        row = self.rows[rid]
        row.update(values, updated_at=datetime.now(timezone.utc))
        return self.view(row, self.case.content_revision)


def service_for(case, failure=False):
    repo = MemoryDustRepository(case)
    calls = []

    def send(req, timeout):
        calls.append(req)
        if req.method == "POST":
            if failure:
                raise TimeoutError()
            return Response({"conversation": {"sId": "c1"}})
        snapshot = next(iter(repo.rows.values()))["input_json"]
        return Response(
            {
                "conversation": {
                    "sId": "c1",
                    "content": [
                        [
                            {
                                "type": "agent_message",
                                "configuration": {
                                    "sId": DEFAULT_AGENTS[snapshot["agent"]]
                                },
                                "status": "succeeded",
                                "content": json.dumps(report_for(snapshot)),
                            }
                        ]
                    ],
                }
            }
        )

    return DustService(repo, client_with(send)), repo, calls


def test_start_refresh_persists_report_without_changing_gemini_or_claim():
    case = media_case()
    original = case.model_dump_json()
    service, repo, calls = service_for(case)
    request = request_for(case)
    run = service.start(str(ACTOR), case.id, request)
    assert run.status == "running"
    assert service.start(str(ACTOR), case.id, request).id == run.id
    assert len(calls) == 1
    repo.rows[run.id]["updated_at"] -= timedelta(seconds=4)
    result = service.refresh(str(ACTOR), case.id, run.id)
    assert result.status == "ready" and result.output.human_review_required
    assert result.gemini_analysis_run_id == case.latest_analysis.id
    assert case.model_dump_json() == original


def test_uncertain_submission_is_not_automatically_resent():
    case = media_case()
    service, repo, calls = service_for(case, failure=True)
    request = request_for(case)
    run = service.start(str(ACTOR), case.id, request)
    assert run.status == "submission_unknown"
    assert service.start(str(ACTOR), case.id, request).id == run.id
    service.refresh(str(ACTOR), case.id, run.id)
    assert len(calls) == 1


def test_stale_case_and_cross_user_requests_never_call_dust():
    case = media_case()
    service, _, calls = service_for(case)
    request = request_for(case)
    request.expected_state_version += 1
    with pytest.raises(StaleCaseError):
        service.start(str(ACTOR), case.id, request)
    with pytest.raises(CaseNotFoundError):
        service.start(str(uuid4()), case.id, request)
    assert calls == []


def test_case_edit_invalidates_pending_result():
    case = media_case()
    service, repo, calls = service_for(case)
    run = service.start(str(ACTOR), case.id, request_for(case))
    case.content_revision += 1
    result = service.refresh(str(ACTOR), case.id, run.id)
    assert result.status == "stale" and result.output is None
    assert len(calls) == 1


def test_api_authentication_and_full_call_path():
    case = media_case()
    service, repo, calls = service_for(case)
    app = create_app()
    app.dependency_overrides[get_dust_service] = lambda: service
    client = TestClient(app)
    request = request_for(case)
    path = f"/v1/cases/{case.id}/dust-runs"
    assert client.post(path, json=request.model_dump(mode="json")).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=str(ACTOR), email="demo@example.invalid"
    )
    response = client.post(path, json=request.model_dump(mode="json"))
    assert response.status_code == 202
    run = next(iter(repo.rows.values()))
    run["updated_at"] -= timedelta(seconds=4)
    result = client.post(f"{path}/{run['id']}/refresh")
    assert result.status_code == 200 and result.json()["status"] == "ready"
    assert len(client.get(path).json()) == 1


def test_migration_is_private_and_idempotency_is_case_scoped(monkeypatch):
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql://test:test@localhost/test?sslmode=require",
    )
    buffer = StringIO()
    config = Config(
        str(Path(__file__).parents[1] / "alembic.ini"), output_buffer=buffer
    )
    command.upgrade(config, "20260926_video_evidence:20260926_dust_runs", sql=True)
    sql = buffer.getvalue().lower()
    assert "enable row level security" in sql
    assert "revoke all on public.dust_runs from public;" in sql
    assert "from pg_catalog.pg_roles" in sql
    assert "where rolname in ('anon', 'authenticated')" in sql
    assert "revoke all on public.dust_runs from %i" in sql
    assert "unique (case_id, idempotency_key)" in sql
    assert "dust_runs_active_agent" in sql


def test_dust_can_cite_a_per_vehicle_liability_finding_from_gemini():
    case = media_case()
    assessment = case.latest_analysis.output.media_analysis.liability.vehicle_assessments[0]
    assessment.citations[0].timestamp_seconds = 2.75
    snapshot = build_dust_input(case, request_for(case, 'legal'), [])
    report = report_for(snapshot)
    report['sources'] = [{'id': 'vehicle-liability', 'title': 'Trajectoire du véhicule',
                          'media_citation': assessment.citations[0].model_dump(mode='json')}]
    assert parse_report(json.dumps(report), snapshot).sources[0].media_citation.timestamp_seconds == 2.75
