from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from claim_api.case_service import CaseService, StaleCaseError
from claim_api.analysis import AnalysisError, AnalysisService, FixtureClaimsAnalyzer, build_analysis_input, validate_output
from claim_api.cctv_fixture import CCTV_FIXTURE_EVENT_ID, CCTV_FIXTURE_VERSION, MOCK_VISION_VERSION, synthetic_observations
from claim_api.camera_service import CameraService
from claim_api.camera_locator import CAMERA_CANDIDATE_ID
from claim_api.models import CreateCameraRequest, CameraRequestTransition, UpdateCameraRequestStatus
from claim_api.models import AnalysisOutputV1, AnalysisProposition, AnalysisRunRequest, IntakePatch
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.video_reuse import PostgresVideoRepository, VideoObservation, VideoSourceChanged, configured_pipeline

REVISION = "20260927_deposit_history"


def _test_database_url() -> str:
    database_url = os.getenv("TEST_MIGRATION_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_MIGRATION_DATABASE_URL to a disposable loopback PostgreSQL test database")
    parsed = make_url(database_url)
    if parsed.host not in {"localhost", "127.0.0.1", "::1"} or "test" not in (parsed.database or "").lower():
        pytest.fail("TEST_MIGRATION_DATABASE_URL must target a loopback database with 'test' in its name")
    return database_url


def _apply_alembic(database_url: str, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_ALLOW_INSECURE_LOCAL", "true")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", database_url)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text("create schema if not exists auth"))
        connection.execute(text("create table if not exists auth.users (id uuid primary key)"))
    engine.dispose()
    api_root = Path(__file__).parents[1]
    command.upgrade(Config(str(api_root / "alembic.ini")), "head")


def test_alembic_applies_head_revision_and_records_it_in_alembic_version(monkeypatch) -> None:
    database_url = _test_database_url()

    _apply_alembic(database_url, monkeypatch)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))

    with engine.connect() as connection:
        recorded = connection.execute(text("select version_num from public.alembic_version")).scalar_one()
        evidence_intents_table = connection.execute(
            text("select to_regclass('public.evidence_upload_intents')")
        ).scalar_one()
        messages_table = connection.execute(
            text("select to_regclass('public.case_messages')")
        ).scalar_one()
        query_json_column = connection.execute(text(
            """select count(*) from information_schema.columns
               where table_schema='public' and table_name='provider_results' and column_name='query_json'"""
        )).scalar_one()
    engine.dispose()
    assert recorded == REVISION
    assert evidence_intents_table == "evidence_upload_intents"
    assert messages_table == "case_messages"
    assert query_json_column == 1


def test_reference_data_migration_is_current_alembic_head() -> None:
    api_root = Path(__file__).parents[1]
    script = ScriptDirectory.from_config(Config(str(api_root / "alembic.ini")))
    assert script.get_current_head() == REVISION


def test_video_artifact_is_unique_and_links_distinct_case_sources(monkeypatch) -> None:
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    actor = uuid4()
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text("insert into auth.users(id) values (:id)"), {"id": str(actor)})
    cases = PostgresCaseRepository(database_url)
    service = CaseService(cases)
    first = service.create_case(str(actor), "g1")
    second = service.create_case(str(actor), "g1")
    first_evidence, second_evidence = uuid4(), uuid4()
    digest = "a" * 64
    with engine.begin() as connection:
        for case, evidence in ((first, first_evidence), (second, second_evidence)):
            connection.execute(text(
                """insert into public.evidence
                   (id, case_id, storage_path, kind, source_kind, mode, mime_type, byte_size,
                    client_sha256, checksum_status, sha256_verified)
                   values (:id,:case_id,:path,'video','handler_upload','live','video/mp4',42,
                           :digest,'verified',:digest)"""
            ), {"id": str(evidence), "case_id": str(case.id),
                "path": f"{case.id}/{evidence}.mp4", "digest": digest})
    registry = PostgresVideoRepository(cases)
    scope = f"user:{actor}"
    spec = configured_pipeline()
    assert registry.claim(scope, digest, spec, "reuse_only")[0] == "not_preprocessed"
    state, artifact = registry.claim(scope, digest, spec, "allow_new")
    assert state == "claimed"
    assert registry.claim(scope, digest, spec, "allow_new")[0] == "processing"
    observation = VideoObservation(start_ms=100, end_ms=300, category="movement", description="Car moves.")
    assert registry.complete(artifact["id"], artifact["attempt_id"], [observation], {"cost_minor": 4})
    assert registry.claim(scope, digest, spec, "reuse_only")[0] == "reused"
    assert registry.link(actor, first.id, first_evidence, artifact["id"])
    assert registry.link(actor, second.id, second_evidence, artifact["id"])
    assert registry.link(actor, second.id, second_evidence, artifact["id"])
    assert not registry.link(uuid4(), first.id, first_evidence, artifact["id"])
    first_view = cases.get_case(actor, first.id)
    second_view = cases.get_case(actor, second.id)
    assert first_view.content_revision == second_view.content_revision == 2
    assert len(first_view.video_analyses) == len(second_view.video_analyses) == 1
    first_snapshot = build_analysis_input(first_view)
    second_snapshot = build_analysis_input(second_view)
    first_refs = [item.source_ref for item in first_snapshot.source_excerpts
                  if item.source_ref.locator == "video:100-300"]
    second_refs = [item.source_ref for item in second_snapshot.source_excerpts
                   if item.source_ref.locator == "video:100-300"]
    assert [ref.id for ref in first_refs] == [str(first_evidence)]
    assert [ref.id for ref in second_refs] == [str(second_evidence)]
    declared_evidence = uuid4()
    with engine.begin() as connection:
        connection.execute(text(
            """insert into public.evidence
               (id, case_id, storage_path, kind, source_kind, mode, mime_type, byte_size,
                client_sha256, checksum_status)
               values (:id,:case_id,:path,'video','handler_upload','live','video/mp4',42,
                       :digest,'client_declared')"""
        ), {"id": str(declared_evidence), "case_id": str(first.id),
            "path": f"{first.id}/{declared_evidence}.mp4", "digest": digest})
    assert registry.verify(actor, first.id, declared_evidence, digest).sha256_verified == digest
    with pytest.raises(VideoSourceChanged):
        registry.verify(actor, first.id, declared_evidence, "b" * 64)
    output = AnalysisOutputV1(schema_version=1, proposed_route="handler_review",
        propositions=[AnalysisProposition(text="A car moves.", assessment="supported", source_refs=first_refs)],
        draft_body="Review the source.")
    validate_output(output, first_snapshot)
    with pytest.raises(AnalysisError):
        validate_output(output, second_snapshot)
    monkeypatch.setenv("VIDEO_PROMPT_VERSION", "changed-prompt")
    assert not any(item.source_ref.locator == "video:100-300"
                   for item in build_analysis_input(first_view).source_excerpts)
    monkeypatch.delenv("VIDEO_PROMPT_VERSION")
    barrier = threading.Barrier(2)
    def concurrent_claim():
        barrier.wait(timeout=5)
        return registry.claim(scope, "b" * 64, spec, "allow_new")[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: concurrent_claim(), range(2)))
    assert sorted(statuses) == ["claimed", "processing"]
    engine.dispose()


def test_postgres_case_repository_versions_changes_and_keeps_action_receipts(monkeypatch) -> None:
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    actor_id = "00000000-0000-4000-8000-000000000042"
    with engine.begin() as connection:
        required_columns = connection.execute(text(
            """select column_name from information_schema.columns
               where table_schema='auth' and table_name='users'
                 and is_nullable='NO' and column_default is null and is_identity='NO'"""
        )).scalars().all()
        if set(required_columns) - {"id"}:
            pytest.skip("repository integration requires the minimal local auth.users test stub")
        connection.execute(
            text("insert into auth.users (id) values (:id) on conflict do nothing"),
            {"id": actor_id},
        )

    service = CaseService(PostgresCaseRepository(database_url))
    created = service.create_case(actor_id, "complete")
    assert created.state_version == 1
    assert created.content_revision == 1
    assert len(created.provider_results) == 2
    assert all(item.mode == "mock" and item.source_id == item.id for item in created.provider_results)
    loaded = service.get_case(actor_id, created.id)
    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.provider_results == created.provider_results
    assert loaded.timeline[0].event_type == "case.created"

    unchanged = service.update_intake(
        actor_id, created.id, 1, IntakePatch(location=created.intake.location)
    )
    assert unchanged.state_version == 1
    assert unchanged.content_revision == 1
    updated = service.update_intake(
        actor_id, created.id, 1, IntakePatch(location="Updated synthetic location")
    )
    assert updated.state_version == 2
    assert updated.content_revision == 2
    with pytest.raises(StaleCaseError) as stale:
        service.update_intake(actor_id, created.id, 1, IntakePatch(narrative="stale"))
    assert stale.value.current_state_version == 2

    draft_id = str(uuid4())
    approval_id = str(uuid4())
    action_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(text(
            """insert into public.drafts (id, case_id, version, content_revision, body, sha256)
               values (:draft, :case, 1, 2, 'synthetic draft', :sha)"""
        ), {"draft": draft_id, "case": str(created.id), "sha": "a" * 64})
        connection.execute(text(
            """update public.cases set current_draft_id=:draft, status='sent', state_version=3
               where id=:case"""
        ), {"draft": draft_id, "case": str(created.id)})
        connection.execute(text(
            """insert into public.approvals
               (id, case_id, draft_id, actor_user_id, draft_sha256, approved_content_revision)
               values (:approval, :case, :draft, :actor, :sha, 2)"""
        ), {
            "approval": approval_id,
            "case": str(created.id),
            "draft": draft_id,
            "actor": actor_id,
            "sha": "a" * 64,
        })
        connection.execute(text(
            """insert into public.actions
               (id, case_id, approval_id, draft_id, kind, mode, status, idempotency_key, payload_hash, reference)
               values (:action, :case, :approval, :draft, 'registration', 'mock', 'confirmed',
                       'test-registration', :hash, 'SIM-CLAIM-TEST')"""
        ), {
            "action": action_id,
            "case": str(created.id),
            "approval": approval_id,
            "draft": draft_id,
            "hash": "b" * 64,
        })

    revised_after_send = service.update_intake(
        actor_id, created.id, 3, IntakePatch(narrative="A revised synthetic report."))
    assert revised_after_send.status == "collecting"
    assert revised_after_send.state_version == 4
    assert revised_after_send.content_revision == 3
    assert revised_after_send.current_draft is None
    assert revised_after_send.approval is None
    assert [action["reference"] for action in revised_after_send.actions] == ["SIM-CLAIM-TEST"]
    engine.dispose()


def test_postgres_evidence_finalize_and_cctv_event(monkeypatch) -> None:
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    actor_id = uuid4()
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        required_columns = connection.execute(text(
            """select column_name from information_schema.columns
               where table_schema='auth' and table_name='users'
                 and is_nullable='NO' and column_default is null and is_identity='NO'"""
        )).scalars().all()
        if set(required_columns) - {"id"}:
            pytest.skip("repository integration requires the minimal local auth.users test stub")
        connection.execute(text("insert into auth.users (id) values (:id)"), {"id": str(actor_id)})

    repository = PostgresCaseRepository(database_url)
    case = CaseService(repository).create_case(str(actor_id), "complete")
    upload_path = f"{case.id}/uploads/{uuid4()}.png"
    assert repository.register_upload_intent(
        actor_id, case.id, case.state_version, upload_path, "scene_photo", "image/png", 123,
        "a" * 64, datetime.now(timezone.utc) + timedelta(hours=2),
    )
    intent = repository.get_upload_intent(actor_id, case.id, upload_path)
    assert intent is not None and intent.finalized_evidence_id is None

    finalized = repository.finalize_evidence(actor_id, case.id, upload_path, case.state_version)
    assert finalized is not None
    assert finalized.state_version == 2 and finalized.content_revision == 2
    assert len(finalized.evidence) == 1
    assert finalized.evidence[0].checksum_status == "client_declared"
    repeated = repository.finalize_evidence(actor_id, case.id, upload_path, case.state_version)
    assert repeated is not None and repeated.state_version == 2

    cctv_id = uuid4()
    cctv = repository.receive_cctv_event(
        actor_id, case.id, CCTV_FIXTURE_EVENT_ID, finalized.state_version, cctv_id,
        f"{case.id}/synthetic-cctv/{CCTV_FIXTURE_EVENT_ID}.png", 456, "b" * 64,
        synthetic_observations(str(cctv_id)), CCTV_FIXTURE_VERSION, MOCK_VISION_VERSION,
    )
    assert cctv is not None
    assert cctv.state_version == 3 and cctv.content_revision == 3
    assert {item.source_kind for item in cctv.evidence} == {"handler_upload", "synthetic_cctv"}
    assert any(item.provider == "mock_vision" for item in cctv.provider_results)
    repeated_cctv = repository.receive_cctv_event(
        actor_id, case.id, CCTV_FIXTURE_EVENT_ID, finalized.state_version, uuid4(),
        f"{case.id}/synthetic-cctv/{CCTV_FIXTURE_EVENT_ID}.png", 456, "b" * 64,
        synthetic_observations(str(cctv_id)), CCTV_FIXTURE_VERSION, MOCK_VISION_VERSION,
    )
    assert repeated_cctv is not None and repeated_cctv.state_version == 3
    engine.dispose()


def test_camera_request_is_local_versioned_and_received_once(monkeypatch) -> None:
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    actor = uuid4()
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text("insert into auth.users(id) values (:id)"), {"id": str(actor)})
    repository = PostgresCaseRepository(database_url)
    camera = CameraService(repository)
    case = CaseService(repository).create_case(str(actor), "complete")
    assert camera.search(str(actor), case.id).status == "candidates"
    draft = camera.create_draft(str(actor), case.id, CreateCameraRequest(
        candidate_id=CAMERA_CANDIDATE_ID,
        scope="Images de la rue de 17:27 à 17:37",
        reason="Vérifier la dynamique déclarée du choc",
        expected_state_version=case.state_version,
    ))
    assert draft.state_version == case.state_version + 1
    assert draft.content_revision == case.content_revision
    assert draft.camera_requests[0].status == "draft"
    approved = camera.approve(str(actor), case.id, draft.camera_requests[0].id,
                              CameraRequestTransition(expected_state_version=draft.state_version))
    request = approved.camera_requests[0]
    assert request.status == "requested" and request.approved_by_user_id == actor
    assert request.requested_at is not None and approved.content_revision == case.content_revision

    evidence_id = uuid4()
    received = repository.receive_cctv_event(
        actor, case.id, CCTV_FIXTURE_EVENT_ID, approved.state_version, evidence_id,
        f"{case.id}/synthetic-cctv/{CCTV_FIXTURE_EVENT_ID}.png", 456, "b" * 64,
        synthetic_observations(str(evidence_id)), CCTV_FIXTURE_VERSION,
        MOCK_VISION_VERSION, request.id,
    )
    assert received is not None
    assert received.camera_requests[0].status == "received"
    assert received.camera_requests[0].evidence_id == evidence_id
    assert received.content_revision == case.content_revision + 1
    again = repository.receive_cctv_event(
        actor, case.id, CCTV_FIXTURE_EVENT_ID, approved.state_version, uuid4(),
        f"{case.id}/synthetic-cctv/{CCTV_FIXTURE_EVENT_ID}.png", 456, "b" * 64,
        synthetic_observations(str(evidence_id)), CCTV_FIXTURE_VERSION,
        MOCK_VISION_VERSION, request.id,
    )
    assert again is not None and again.content_revision == received.content_revision
    assert len([item for item in again.evidence if item.source_kind == "synthetic_cctv"]) == 1

    blocked = CaseService(repository).create_case(str(actor), "complete")
    blocked_draft = camera.create_draft(str(actor), blocked.id, CreateCameraRequest(
        candidate_id=CAMERA_CANDIDATE_ID, scope="Images du créneau déclaré",
        reason="Vérifier le récit de l'assurée", expected_state_version=blocked.state_version,
    ))
    with pytest.raises(ValueError):
        repository.receive_cctv_event(
            actor, blocked.id, CCTV_FIXTURE_EVENT_ID, blocked_draft.state_version,
            uuid4(), f"{blocked.id}/synthetic-cctv/{CCTV_FIXTURE_EVENT_ID}.png",
            456, "b" * 64, {}, CCTV_FIXTURE_VERSION, MOCK_VISION_VERSION,
            blocked_draft.camera_requests[0].id,
        )
    approved_blocked = camera.approve(str(actor), blocked.id,
        blocked_draft.camera_requests[0].id,
        CameraRequestTransition(expected_state_version=blocked_draft.state_version))
    denied = camera.set_status(str(actor), blocked.id, approved_blocked.camera_requests[0].id,
        UpdateCameraRequestStatus(status="denied", expected_state_version=approved_blocked.state_version))
    assert denied.camera_requests[0].status == "denied"
    assert denied.evidence == [] and denied.content_revision == blocked.content_revision
    engine.dispose()


def test_postgres_analysis_run_persists_blocked_gate_and_rejects_mid_run_edit(monkeypatch) -> None:
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    actor_id = uuid4()
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        required_columns = connection.execute(text(
            """select column_name from information_schema.columns
               where table_schema='auth' and table_name='users'
                 and is_nullable='NO' and column_default is null and is_identity='NO'"""
        )).scalars().all()
        if set(required_columns) - {"id"}:
            pytest.skip("repository integration requires the minimal local auth.users test stub")
        connection.execute(text("insert into auth.users (id) values (:id)"), {"id": str(actor_id)})
    engine.dispose()

    repository = PostgresCaseRepository(database_url)
    case = CaseService(repository).create_case(str(actor_id), "complete")
    result = AnalysisService(repository, FixtureClaimsAnalyzer()).run(
        str(actor_id), case.id, AnalysisRunRequest(expected_state_version=case.state_version),
    )
    assert result.analysis_run.status == "ready"
    assert result.analysis_run.mode == "mock"
    assert [gate.status for gate in result.analysis_run.gate_results] == ["passed", "blocked", "passed"]
    assert result.analysis_run.gate_results[1].reason_codes == ["corroborated_vehicle_association_required"]
    assert result.case.current_draft is None
    loaded = CaseService(repository).get_case(str(actor_id), case.id)
    assert loaded.latest_analysis == result.analysis_run
    assert loaded.current_draft == result.case.current_draft

    stale_case = CaseService(repository).create_case(str(actor_id), "complete")

    class EditingAnalyzer(FixtureClaimsAnalyzer):
        def analyze(self, analysis_input):
            CaseService(repository).update_intake(
                str(actor_id), stale_case.id, stale_case.state_version,
                IntakePatch(narrative="Updated while the synthetic analysis was running."),
            )
            return super().analyze(analysis_input)

    stale = AnalysisService(repository, EditingAnalyzer()).run(
        str(actor_id), stale_case.id,
        AnalysisRunRequest(expected_state_version=stale_case.state_version),
    )
    assert stale.analysis_run.status == "stale"
    assert stale.case.current_draft is None
    assert stale.case.content_revision == stale_case.content_revision + 1


def test_postgres_analysis_retry_recovers_timed_out_running_run_with_audit(monkeypatch) -> None:
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    actor_id = uuid4()
    with engine.begin() as connection:
        required_columns = connection.execute(text(
            """select column_name from information_schema.columns
               where table_schema='auth' and table_name='users'
                 and is_nullable='NO' and column_default is null and is_identity='NO'"""
        )).scalars().all()
        if set(required_columns) - {"id"}:
            pytest.skip("analysis integration requires the minimal local auth.users test stub")
        connection.execute(
            text("insert into auth.users (id) values (:id) on conflict do nothing"),
            {"id": str(actor_id)},
        )

    repository = PostgresCaseRepository(database_url)
    created = CaseService(repository).create_case(str(actor_id), "complete")
    orphan_id = uuid4()
    with engine.begin() as connection:
        connection.execute(text(
            """insert into public.analysis_runs
               (id, case_id, input_content_revision, method_version, status, input_json, output_json, started_at)
               values (:id, :case_id, :revision, 'claims-analysis-v1.0.0', 'running',
                       cast(:input_json as jsonb), cast(:output_json as jsonb), :started_at)"""
        ), {
            "id": str(orphan_id),
            "case_id": str(created.id),
            "revision": created.content_revision,
            "input_json": "{}",
            "output_json": '{"mode":"live"}',
            "started_at": datetime.now(timezone.utc) - timedelta(minutes=10),
        })

    result = AnalysisService(repository, FixtureClaimsAnalyzer()).run(
        str(actor_id), created.id, AnalysisRunRequest(expected_state_version=created.state_version),
    )
    assert result.analysis_run.status == "ready"
    assert result.case.current_draft is None
    assert result.analysis_run.gate_results[1].reason_codes == ["corroborated_vehicle_association_required"]

    with engine.connect() as connection:
        orphan = connection.execute(text(
            "select status, error_code, output_json from public.analysis_runs where id=:id"
        ), {"id": str(orphan_id)}).mappings().one()
        event = connection.execute(text(
            """select event_type, state_version_before, state_version_after,
                      content_revision_before, content_revision_after, metadata_json
               from public.audit_events where case_id=:case_id
                 and event_type='case.analysis_timeout_recovered'"""
        ), {"case_id": str(created.id)}).mappings().one()
    engine.dispose()

    assert orphan["status"] == "failed"
    assert orphan["error_code"] == "analysis_timeout_recovered"
    assert orphan["output_json"]["mode"] == "live"
    assert orphan["output_json"]["error_message"]
    assert event["state_version_before"] == event["state_version_after"] == created.state_version
    assert event["content_revision_before"] == event["content_revision_after"] == created.content_revision
    assert event["metadata_json"]["analysis_run_id"] == str(orphan_id)
    assert event["metadata_json"]["reason_code"] == "analysis_timeout_recovered"
