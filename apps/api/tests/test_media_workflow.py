from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from claim_api.dust_client import DustError
from claim_api.media_insurance import lookup_media_plates
from claim_api.media_workflow import LegacyMediaWorkflow as MediaWorkflow, MediaWorkflowRepository
from claim_api.models import PlateFinding
from test_dust import media_case
from test_analysis import ACTOR


def plate_case(plate="AB12 CDE", legibility="readable"):
    case = media_case()
    case.intake.incident_at = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
    case.latest_analysis.output.media_analysis.plates = [
        PlateFinding(
            description="Plaque visible sur le véhicule sombre",
            confidence="high",
            vehicle="véhicule sombre",
            plate=plate,
            legibility=legibility,
            role="third_party",
            citations=[{"evidence_id": case.evidence[1].id, "timestamp_seconds": 2.5}],
        )
    ]
    return case


@pytest.mark.parametrize(
    "plate,insurer",
    [("AB12 CDE", "Northbridge Demo Motor"), ("FR-482-KL", "Azur Demo Auto")],
)
def test_gemini_plates_use_exact_runtime_dataset_and_preserve_source(plate, insurer):
    case = plate_case(plate)
    result = lookup_media_plates(case)[0]
    assert result["status"] == "matched"
    assert insurer in result["data"]["insurer_name"]
    assert result["synthetic"] and result["requires_human_review"]
    assert result["analysis_run_id"] == str(case.latest_analysis.id)
    assert result["citations"][0]["timestamp_seconds"] == 2.5
    assert result["query"]["incident_date"] == "2026-09-25"


@pytest.mark.parametrize(
    "plate,legibility,status",
    [
        ("AB12 CD?", "partial", "ambiguous"),
        ("ZZ99 ZZZ", "readable", "no_match"),
        ("AB13 CDE", "readable", "no_match"),
        ("AB12 CDE", "partial", "ambiguous"),
    ],
)
def test_partial_absent_and_expired_plates_are_not_promoted_to_insured(
    plate, legibility, status
):
    result = lookup_media_plates(plate_case(plate, legibility))[0]
    assert result["status"] == status
    if legibility == "partial":
        assert result["data"] == {}


def test_missing_date_and_stale_analysis_do_not_create_insurance_matches():
    case = plate_case()
    case.intake.incident_at = None
    assert lookup_media_plates(case)[0]["reason"] == "incident_date_required"
    case.content_revision += 1
    assert lookup_media_plates(case) == []


class Repo:
    def __init__(self, case):
        self.case = case
        self.job = {
            "id": uuid4(),
            "actor_id": ACTOR,
            "case_id": case.id,
            "content_revision": case.content_revision,
            "generation": 1,
            "attempts": 0,
            "lease_id": uuid4(),
            "status": "queued",
        }
        self.drafts = []

    def get_case(self, *_):
        return self.case

    def claim(self, *_):
        if self.job["status"] not in {"queued", "waiting"}:
            return None
        self.job["status"] = "processing"
        self.job["attempts"] += 1
        return deepcopy(self.job)

    def finish(self, job, status, **values):
        self.job.update(status=status, **values)

    def checkpoint(self, job, matches, analysis_id):
        self.job.update(matches=matches, analysis_id=analysis_id)

    def save_garage_draft(self, actor, case, run):
        self.drafts.append(run.output.draft_body)


class Dust:
    def __init__(self, status="running"):
        self.repository = self
        self.run = SimpleNamespace(
            id=uuid4(),
            status=status,
            error_code=None,
            input_content_revision=1,
            output=SimpleNamespace(
                draft_body="Bonjour, merci de nous proposer un devis détaillé."
            ),
        )
        self.started = 0
        self.keys = []

    def find_request(self, *_):
        return {"id": self.run.id} if self.started else None

    def start(self, actor, case_id, request):
        self.started += 1
        self.keys.append(request.idempotency_key)
        return self.run

    def refresh(self, *_):
        return self.run


def workflow(case=None, dust=None):
    case = case or plate_case()
    repo = Repo(case)
    calls = []

    def run(*args, **kwargs):
        calls.append(case.id)
        return SimpleNamespace(case=case, analysis_run=case.latest_analysis)

    service = MediaWorkflow(repo, media=SimpleNamespace(run=run), dust=dust or Dust())
    return service, repo, calls


def test_media_then_lookup_then_garage_runs_once_and_saves_ready_draft():
    service, repo, _ = workflow()
    service.tick()
    assert repo.job["matches"][0]["status"] == "matched"
    assert repo.job["status"] == "waiting" and service.dust.started == 1
    service.dust.run.status = "ready"
    service.tick()
    assert repo.job["status"] == "ready" and service.dust.started == 1
    assert repo.drafts == [service.dust.run.output.draft_body]
    assert not service.tick()


def test_failed_gemini_preserves_files_and_does_not_call_dust():
    case = plate_case()
    case.latest_analysis.status = "failed"
    case.latest_analysis.error_code = "gemini_rate_limited"
    service, repo, _ = workflow(case)
    service.tick()
    assert repo.job["status"] == "waiting" and repo.job["error"] == "gemini_rate_limited"
    service.tick()
    service.tick()
    assert repo.job["status"] == "failed" and not service.tick()
    assert service.dust.started == 0 and len(case.evidence) == 2


def test_new_revision_disables_old_queued_jobs_without_provider_calls():
    service, repo, calls = workflow()
    repo.case.content_revision += 1
    service.tick()
    assert repo.job["status"] == "stale" and calls == [] and service.dust.started == 0


def test_unconfigured_dust_keeps_gemini_and_lookup_results():
    dust = Dust()

    def reject(*_):
        raise DustError("dust_not_configured")

    dust.start = reject
    service, repo, _ = workflow(dust=dust)
    service.tick()
    assert repo.job["error"] == "dust_not_configured"
    assert repo.job["matches"][0]["status"] == "matched"
    assert repo.case.latest_analysis.status == "ready"


def test_transient_dust_read_failure_keeps_polling_the_same_conversation():
    service, repo, _ = workflow()
    service.tick()
    original_refresh = service.dust.refresh

    def unavailable(*_):
        raise DustError("dust_rate_limited")

    service.dust.refresh = unavailable
    service.tick()
    assert repo.job["status"] == "waiting"
    assert service.dust.started == 1
    service.dust.refresh = original_refresh
    service.dust.run.status = "ready"
    service.tick()
    assert repo.job["status"] == "ready" and service.dust.started == 1


@pytest.mark.parametrize("status", ["submission_unknown", "needs_action"])
def test_uncertain_dust_submission_never_creates_another_conversation(status):
    service, repo, _ = workflow(dust=Dust(status))
    service.tick()
    assert repo.job["status"] == "needs_action"
    assert not service.tick() and service.dust.started == 1


def test_worker_route_requires_server_secret_and_manager_route_requires_login(
    monkeypatch,
):
    from claim_api.main import create_app
    from claim_api.routes.media_workflow import workflow as dependency

    calls = []
    app = create_app()
    app.dependency_overrides[dependency] = lambda: SimpleNamespace(
        tick=lambda: calls.append(1)
    )
    monkeypatch.setenv("MEDIA_WORKER_TOKEN", "x" * 40)
    client = TestClient(app)
    assert client.post("/internal/media-workflow/tick").status_code == 401
    assert (
        client.post(
            "/internal/media-workflow/tick", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert calls == []
    assert (
        client.post(
            "/internal/media-workflow/tick",
            headers={"Authorization": "Bearer " + "x" * 40},
        ).status_code
        == 200
    )
    assert calls == [1]
    assert client.get(f"/v1/cases/{uuid4()}/automation").status_code == 401


def test_queue_lease_prevents_two_workers_and_fences_expired_owner(sms_db):
    engine, _, actor, _, case_id, _ = sms_db
    from test_alembic_version import _test_database_url

    repo = MediaWorkflowRepository(_test_database_url())
    case = repo.get_case(actor, case_id)
    repo.enqueue(actor, case)
    repo.enqueue(actor, case)
    job = repo.claim(case_id)
    assert job and repo.claim(case_id) is None
    with repo._connection() as conn:
        conn.execute(
            "update public.case_media_workflows set lease_until=now()-interval '1 second' where id=%s",
            (job["id"],),
        )
    reclaimed = repo.claim(case_id)
    assert reclaimed["lease_id"] != job["lease_id"]
    repo.finish(job, "ready")
    assert repo.snapshot(actor, case_id)["workflow"]["status"] == "processing"
    repo.finish(reclaimed, "ready")
    assert repo.snapshot(actor, case_id)["workflow"]["status"] == "ready"


from test_sms_service import sms_db  # shared disposable DB fixture


from test_insured_portal import portal_db


def test_insured_photo_then_video_traverse_real_storage_validation_gemini_and_dust(
    portal_db, monkeypatch
):
    """Actual portal factory, SQL queue, Gemini/Dust clients; only provider HTTP is simulated."""
    import hashlib
    from claim_api.gemini_analysis import GeminiClaimsAnalyzer
    from claim_api.media_analysis import MediaAnalysisService
    from claim_api.dust_service import DustService
    from claim_api.dust_repository import PostgresDustRepository
    from claim_api.models import (
        CreateEvidenceUploadIntentRequest,
        FinalizeEvidenceRequest,
        MediaAnalysis,
    )
    from claim_api.storage import StoredObjectInfo
    from test_evidence_service import FakeStorage
    from test_gemini_analysis import GeminiServer
    from test_dust import client_with, Response, report_for

    database_url, _, actor, first, _, _, portal, _, token, _, _ = portal_db
    repo = MediaWorkflowRepository(database_url)
    storage = FakeStorage()
    server = GeminiServer(None)
    dust_submissions = []
    conversation_prefix = uuid4().hex
    dust_read_denied = True

    class Gemini(GeminiClaimsAnalyzer):
        def analyze(self, snapshot):
            evidence = snapshot.evidence[-1]
            citation = {"evidence_id": evidence.id}
            if evidence.mime_type.startswith("video/"):
                citation["timestamp_seconds"] = 1.0
            server.output = MediaAnalysis(
                analyzed_evidence_ids=[item.id for item in snapshot.evidence],
                summary="Pare-chocs endommagé.",
                plates=[
                    dict(
                        vehicle="véhicule sombre",
                        plate="FR-482-KL",
                        legibility="readable",
                        role="unknown",
                        description="Plaque lisible",
                        confidence="high",
                        citations=[citation],
                    )
                ],
                damages=[dict(vehicle="véhicule sombre", description="Dégâts non visibles sur cette vue",
                    confidence="low", citations=[citation], affected_parts=[], severity="unknown",
                    accident_link="uncertain", estimate=None)],
                liability=dict(
                    vehicle_assessments=[dict(vehicle="véhicule sombre", role="unknown", assessment="undetermined",
                        reasoning="Séquence incomplète", confidence="low", citations=[citation])],
                    likely_responsible="undetermined",
                    reasoning="Séquence incomplète",
                    confidence="low",
                    limitations=["Circonstances à confirmer"],
                ),
                cross_evidence_consistency="Même véhicule à confirmer",
                limitations=["Pas de devis"],
            )
            return super().analyze(snapshot)

    def dust_http(request, timeout):
        nonlocal dust_read_denied
        if request.method == "POST":
            dust_submissions.append(request)
            # Gemini output is already committed before the garage call.
            assert repo.get_case(actor, first.id).latest_analysis.status == "ready"
            return Response(
                {"conversation": {"sId": f"{conversation_prefix}-{len(dust_submissions)}"}}
            )
        if dust_read_denied:
            from urllib.error import HTTPError

            dust_read_denied = False
            raise HTTPError(request.full_url, 403, "denied", {}, None)
        conversation_id = request.full_url.rsplit("/", 1)[-1]
        with repo._connection() as conn:
            row = conn.execute(
                "select * from public.dust_runs where conversation_id=%s",
                (conversation_id,),
            ).fetchone()
        report = report_for(row["input_json"])
        report["draft_body"] = (
            "Bonjour, merci de proposer un devis détaillé après inspection du véhicule."
        )
        import json

        return Response(
            {
                "conversation": {
                    "sId": conversation_id,
                    "content": [
                        [
                            {
                                "type": "agent_message",
                                "status": "succeeded",
                                "configuration": {"sId": row["agent_id"]},
                                "content": json.dumps(report),
                            }
                        ]
                    ],
                }
            }
        )

    analyzer = Gemini(
        api_key="test-key",
        media_only=True,
        open_url=server,
        sleep=lambda _: None,
        storage=SimpleNamespace(
            read_object=lambda path, limit: storage.object_bytes[path]
        ),
    )
    flow = MediaWorkflow(
        repo,
        media=MediaAnalysisService(repo, analyzer=analyzer),
        dust=DustService(PostgresDustRepository(database_url), client_with(dust_http)),
    )
    monkeypatch.setattr(
        "claim_api.insured_portal.SupabaseStorageAdapter.from_env", lambda: storage
    )
    monkeypatch.setattr("claim_api.media_workflow.MediaWorkflow", lambda _: flow)
    session = portal.exchange(token)["session_token"]
    for index, (filename, mime, kind, content) in enumerate(
        [
            (
                "photo.png",
                "image/png",
                "damage_photo",
                b"\x89PNG\r\n\x1a\n" + b"\0" * 500,
            ),
            (
                "video.mp4",
                "video/mp4",
                "scene_video",
                b"\x00\x00\x00\x18ftypisom" + b"\0" * 500,
            ),
        ],
        start=1,
    ):
        current = portal.summary(session)
        checksum = hashlib.sha256(content).hexdigest()
        intent = portal.create_upload_intent(
            session,
            CreateEvidenceUploadIntentRequest(
                filename=filename,
                mime_type=mime,
                kind=kind,
                byte_size=len(content),
                client_sha256=checksum,
                expected_state_version=current["state_version"],
            ),
        )
        storage.objects[intent.storage_path] = StoredObjectInfo(
            byte_size=len(content), mime_type=mime
        )
        storage.object_bytes[intent.storage_path] = content
        request = FinalizeEvidenceRequest(
            storage_path=intent.storage_path,
            client_sha256=checksum,
            kind=kind,
            expected_state_version=current["state_version"],
        )
        received = portal.finalize_upload(session, request)
        case = repo.get_case(actor, first.id)
        assert len(received["evidence"]) == index
        assert case.latest_analysis.status == "ready", case.latest_analysis.error_code
        assert (
            len(case.latest_analysis.output.media_analysis.analyzed_evidence_ids)
            == index
        )
        assert len(dust_submissions) == index
        assert server.generation_calls == index
        # A network retry of finalization creates neither a duplicate file nor another API call.
        portal.finalize_upload(session, request)
        assert server.generation_calls == index and len(dust_submissions) == index
        with repo._connection() as conn:
            conn.execute(
                "update public.case_media_workflows set available_at=now() where case_id=%s",
                (first.id,),
            )
            conn.execute(
                "update public.dust_runs set updated_at=now()-interval '5 seconds' where case_id=%s",
                (first.id,),
            )
        flow.tick(first.id)
        if index == 1:
            assert (
                repo.snapshot(actor, first.id)["workflow"]["error_code"]
                == "dust_forbidden"
            )
            repo.enqueue(actor, repo.get_case(actor, first.id), retry=True)
            flow.tick(first.id)
            assert (
                len(dust_submissions) == 1
            )  # Retry GET; do not create a competing conversation.
        saved = repo.snapshot(actor, first.id)
        assert saved["workflow"]["status"] == "ready", saved["workflow"]["error_code"]
        assert saved["workflow"]["insurance_matches"][0]["plate"] == "FR-482-KL"
        assert len(saved["correspondence"]) == index
        assert saved["correspondence"][0]["status"] == "draft"
        assert saved["correspondence"][0]["content_revision"] == case.content_revision


def test_automatic_retry_recovers_gemini_before_starting_garage():
    case = plate_case()
    ready = case.latest_analysis.model_copy(deep=True)
    case.latest_analysis.status = 'failed'
    case.latest_analysis.error_code = 'gemini_rate_limited'
    service, repo, _ = workflow(case)
    policies = []
    def run(*args, automatic=False, **kwargs):
        policies.append(automatic)
        if not automatic:
            case.latest_analysis = ready
        return SimpleNamespace(case=case, analysis_run=case.latest_analysis)
    service.media.run = run
    service.tick()
    assert repo.job['status'] == 'waiting' and service.dust.started == 0
    service.tick()
    assert policies == [True, False] and service.dust.started == 1
    assert repo.job['analysis_id'] == ready.id


def test_demo_receipt_queues_after_commit_and_replay_does_not_analyze_twice(portal_db, monkeypatch):
    from claim_api.storage import SupabaseStorageAdapter
    from test_evidence_service import FakeStorage
    database_url, _, actor, first, _, _, portal, _, token, _, _ = portal_db
    repo = MediaWorkflowRepository(database_url)
    monkeypatch.setattr(SupabaseStorageAdapter, 'from_env', lambda: FakeStorage())
    processed = []
    def tick(case_id):
        # A separate connection proves evidence and work are visible after commit.
        current = repo.get_case(actor, case_id)
        assert current.evidence
        assert repo.snapshot(actor, case_id)['workflow']['status'] == 'queued'
        processed.append(case_id)
    monkeypatch.setattr('claim_api.media_workflow.MediaWorkflow', lambda _: SimpleNamespace(tick=tick))
    session = portal.exchange(token)['session_token']
    key = portal.demo_media(session)[0]['id']
    portal.attach_demo(session, key, first.state_version)
    portal.attach_demo(session, key, first.state_version)
    assert processed == [first.id]


def test_camera_and_seed_receipts_queue_even_without_post_commit_callback(sms_db):
    from claim_api.case_service import CaseService
    from claim_api.evidence_service import EvidenceService
    from claim_api.cctv_fixture import CCTV_FIXTURE_EVENT_ID
    from claim_api.models import ReceiveCCTVRequest, SeedG1MediaRequest
    from test_evidence_service import FakeStorage
    from test_alembic_version import _test_database_url
    _, _, actor, _, _, _ = sms_db
    repo = MediaWorkflowRepository(_test_database_url())
    service = EvidenceService(repo, FakeStorage())
    for scenario in ('complete', 'g1'):
        case = CaseService(repo).create_case(str(actor), scenario)
        if scenario == 'complete':
            updated = service.receive_cctv(str(actor), case.id,
                ReceiveCCTVRequest(fixture_event_id=CCTV_FIXTURE_EVENT_ID, expected_state_version=case.state_version))
        else:
            updated = service.seed_g1_media(str(actor), case.id,
                SeedG1MediaRequest(expected_state_version=case.state_version))
        job = repo.snapshot(actor, case.id)['workflow']
        assert job['status'] == 'queued' and job['content_revision'] == updated.content_revision
