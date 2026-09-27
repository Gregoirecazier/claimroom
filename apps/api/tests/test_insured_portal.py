from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from claim_api.case_service import CaseReadOnlyError, CaseService, StaleCaseError
from claim_api.deposit_grants import DepositGrantError, DepositGrantService
from claim_api.demo_media import CATALOGUES, MEDIA_ROOT
from claim_api.evidence_service import EvidenceNotFoundError, EvidenceService, InvalidEvidenceUploadError
from claim_api.g1_media import G1_MEDIA_DIR, G1_MEDIA_VERSION
from claim_api.insured_portal import InsuredPortalService
from claim_api.main import create_app
from claim_api.models import CreateEvidenceUploadIntentRequest, FinalizeEvidenceRequest
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.routes.insured_portal import get_portal_service
from claim_api.storage import StorageAdapterError, StoredObjectInfo
from test_alembic_version import _apply_alembic, _test_database_url
from test_evidence_service import FakeStorage


@pytest.fixture
def portal_db(monkeypatch):
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    actor = uuid4()
    with engine.begin() as connection:
        connection.execute(text("insert into auth.users(id) values (:id)"), {"id": actor})
    cases = CaseService(PostgresCaseRepository(database_url))
    first = cases.create_case(str(actor), "g1")
    second = cases.create_case(str(actor), "g1")
    grants = DepositGrantService(database_url, "https://claim.example/depot")
    portal = InsuredPortalService(database_url)

    def sent_link(case):
        issued = grants.issue(case.id, case.content_revision, timedelta(hours=2), actor_id=actor)
        with grants._connect() as connection, connection.cursor() as cursor:
            grants.validate_url(cursor, issued.grant_id, case.id, issued.url, case.content_revision)
            cursor.execute("""insert into public.case_messages
                (case_id, recipient, body, provider, idempotency_key, payload_hash)
                values (%s,'+33100000000',%s,'mock',%s,%s) returning id""",
                (case.id, issued.url, str(uuid4()), "a" * 64))
            message_id = cursor.fetchone()["id"]
            grants.associate(cursor, issued.grant_id, message_id, case.id)
        return issued, issued.url.split("#token=", 1)[1]

    first_link, first_token = sent_link(first)
    second_link, second_token = sent_link(second)
    yield (database_url, engine, actor, first, second, grants, portal,
           first_link, first_token, second_link, second_token)
    engine.dispose()


def test_guest_session_is_case_scoped_correction_versioned_and_revocable(portal_db):
    (_url, engine, actor, first, second, grants, portal,
     first_link, first_token, _second_link, second_token) = portal_db
    first_session = portal.exchange(first_token)["session_token"]
    second_session = portal.exchange(second_token)["session_token"]
    first_summary = portal.summary(first_session)
    assert first_summary["case_id"] == first.id
    assert first_summary["source_label"] == "Selon votre déclaration"
    assert "provider_results" not in first_summary
    assert "insured_name" in first_summary["intake"]
    assert portal.summary(second_session)["case_id"] == second.id
    changed = portal.correct(first_session, first_summary["state_version"], "location", "Lille", "Correction")
    assert changed["content_revision"] == first.content_revision + 1
    assert changed["intake"]["location"] == "Lille"
    assert portal.summary(second_session)["content_revision"] == second.content_revision
    with engine.connect() as connection:
        correction = connection.execute(text("""select field_name, content_revision,
            previous_value_json, new_value_json from public.insured_corrections
            where case_id=:id"""), {"id": first.id}).one()
        assert correction.field_name == "location"
        assert correction.content_revision == changed["content_revision"]
        assert correction.new_value_json == {"value": "Lille"}
        assert correction.previous_value_json != correction.new_value_json
    with pytest.raises(StaleCaseError):
        portal.correct(first_session, first_summary["state_version"], "narrative", "Ancien", None)
    with pytest.raises(DepositGrantError, match="forbidden_capability"):
        portal.correct(first_session, changed["state_version"], "insured_identity_source", "handler_entered", None)
    grants.revoke(first_link.grant_id, first.id, actor_id=actor)
    with pytest.raises(DepositGrantError, match="revoked_grant"):
        portal.summary(first_session)
    with pytest.raises(DepositGrantError, match="revoked_grant"):
        portal.exchange(first_token)
    assert portal.summary(second_session)["case_id"] == second.id


@pytest.mark.parametrize("mutation", ["correct", "attach_demo"])
def test_guest_mutation_rechecks_revocation_after_initial_authorization(portal_db, monkeypatch, mutation):
    (database_url, engine, actor, first, _second, grants, portal,
     first_link, first_token, _second_link, _second_token) = portal_db
    session = portal.exchange(first_token)["session_token"]
    if mutation == "attach_demo":
        monkeypatch.setattr("claim_api.insured_portal.SupabaseStorageAdapter.from_env", lambda: FakeStorage())

    # Revoke exactly after the portal's initial authorization and before its
    # SELECT FOR UPDATE on the case. This catches a stale grant read in the
    # same transaction, including the demo path after its Storage write.
    original_connect = portal._connect
    revoked = False

    class CursorProxy:
        def __init__(self, cursor):
            self.cursor = cursor

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self.cursor.__exit__(*args)

        def execute(self, query, params=None):
            nonlocal revoked
            if "select * from public.cases where id=%s for update" in query and not revoked:
                revoked = True
                grants.revoke(first_link.grant_id, first.id, actor_id=actor)
            return self.cursor.execute(query, params)

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def cursor(self):
            return CursorProxy(self.connection.cursor())

    monkeypatch.setattr(portal, "_connect", lambda: ConnectionProxy(original_connect()))
    with pytest.raises(DepositGrantError, match="revoked_grant"):
        if mutation == "correct":
            portal.correct(session, first.state_version, "location", "Lille", "Correction")
        else:
            portal.attach_demo(session, "photo-detail.png", first.state_version)
    assert revoked
    with engine.connect() as connection:
        state = connection.execute(text("select state_version, content_revision from public.cases where id=:id"),
                                   {"id": first.id}).one()
        assert (state.state_version, state.content_revision) == (first.state_version, first.content_revision)
        assert connection.execute(text("select count(*) from public.insured_corrections where case_id=:id"),
                                  {"id": first.id}).scalar_one() == 0
        assert connection.execute(text("select count(*) from public.deposit_demo_media where case_id=:id"),
                                  {"id": first.id}).scalar_one() == 0


def test_unassociated_expired_and_foreign_media_are_denied(portal_db):
    (_url, engine, actor, first, second, grants, portal,
     _first_link, first_token, _second_link, second_token) = portal_db
    preview = grants.issue(first.id, first.content_revision, actor_id=actor)
    with pytest.raises(DepositGrantError, match="invalid_grant"):
        portal.exchange(preview.url.split("#token=", 1)[1])
    first_session = portal.exchange(first_token)["session_token"]
    second_session = portal.exchange(second_token)["session_token"]
    with engine.begin() as connection:
        connection.execute(text("""insert into public.evidence
            (case_id, storage_path, kind, source_kind, mode, mime_type, byte_size, checksum_status)
            values (:id, :path, 'scene_photo', 'handler_upload', 'live', 'image/png', 10, 'client_declared')"""),
            {"id": second.id, "path": f"{second.id}/foreign.png"})
        foreign_id = connection.execute(text("select id from public.evidence where case_id=:id limit 1"),
                                        {"id": second.id}).scalar_one()
    with pytest.raises(EvidenceNotFoundError):
        portal.signed_read_url(first_session, foreign_id)
    with pytest.raises(EvidenceNotFoundError):
        portal.signed_read_url(second_session, foreign_id)  # Handler files stay private.
    with engine.begin() as connection:
        connection.execute(text("""update public.deposit_sessions
            set created_at=now()-interval '2 hours', expires_at=now()-interval '1 second'
            where token_sha256=:digest"""), {"digest": hashlib.sha256(first_session.encode()).hexdigest()})
    with pytest.raises(DepositGrantError, match="expired_session"):
        portal.summary(first_session)
    assert portal.exchange(first_token)["session_token"] != first_session


def test_guest_upload_uses_existing_validation_and_preserves_provenance(portal_db, monkeypatch):
    (database_url, engine, _actor, first, second, _grants, portal,
     _first_link, first_token, _second_link, second_token) = portal_db
    fake_storage = FakeStorage()
    monkeypatch.setattr(portal, "_evidence_service", lambda: EvidenceService(PostgresCaseRepository(database_url), fake_storage))
    first_session = portal.exchange(first_token)["session_token"]
    second_session = portal.exchange(second_token)["session_token"]
    video_bytes = b"\x00\x00\x00\x18ftypisom" + b"\0" * 500
    digest = hashlib.sha256(video_bytes).hexdigest()
    request = CreateEvidenceUploadIntentRequest(filename="scene.mp4", mime_type="video/mp4",
        byte_size=len(video_bytes), client_sha256=digest, kind="scene_video", expected_state_version=first.state_version)
    intent = portal.create_upload_intent(first_session, request)
    assert intent.storage_path.startswith(f"{first.id}/uploads/")
    with engine.connect() as connection:
        row = connection.execute(text("""select source_kind, deposit_grant_id
            from public.evidence_upload_intents where storage_path=:path"""), {"path": intent.storage_path}).one()
        assert row.source_kind == "insured_upload" and row.deposit_grant_id is not None
    with pytest.raises(EvidenceNotFoundError):
        portal.finalize_upload(second_session, FinalizeEvidenceRequest(storage_path=intent.storage_path,
            client_sha256=digest, kind="scene_video", expected_state_version=second.state_version))
    with pytest.raises(InvalidEvidenceUploadError):
        portal.finalize_upload(first_session, FinalizeEvidenceRequest(storage_path=intent.storage_path,
            client_sha256=digest, kind="scene_video", expected_state_version=first.state_version))
    fake_storage.objects[intent.storage_path] = StoredObjectInfo(byte_size=len(video_bytes), mime_type="video/mp4")
    fake_storage.object_bytes[intent.storage_path] = b"wrong content" + b"\0" * (len(video_bytes)-13)
    with pytest.raises(InvalidEvidenceUploadError):
        portal.finalize_upload(first_session, FinalizeEvidenceRequest(storage_path=intent.storage_path,
            client_sha256=digest, kind="scene_video", expected_state_version=first.state_version))
    fake_storage.object_bytes[intent.storage_path] = video_bytes
    result = portal.finalize_upload(first_session, FinalizeEvidenceRequest(storage_path=intent.storage_path,
        client_sha256=digest, kind="scene_video", expected_state_version=first.state_version))
    assert result["content_revision"] == first.content_revision + 1
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["source_kind"] == "insured_upload"
    assert result["evidence"][0]["checksum_status"] == "verified"
    assert portal.summary(second_session)["evidence"] == []
    read = portal.signed_read_url(first_session, result["evidence"][0]["id"])
    assert read["url"].startswith("https://project.example/")


@pytest.mark.parametrize("invalidation, error_code", [
    ("revoke", "revoked_grant"),
    ("expire", "expired_grant"),
])
def test_grant_rechecked_inside_upload_transactions(portal_db, invalidation, error_code):
    (database_url, engine, actor, first, _second, grants, _portal,
     first_link, _first_token, _second_link, _second_token) = portal_db
    storage = FakeStorage()
    repository = PostgresCaseRepository(database_url)
    evidence_service = EvidenceService(repository, storage)
    video_bytes = b"\x00\x00\x00\x18ftypisom" + b"\0" * 500
    digest = hashlib.sha256(video_bytes).hexdigest()
    request = CreateEvidenceUploadIntentRequest(
        filename="scene.mp4", mime_type="video/mp4", byte_size=len(video_bytes),
        client_sha256=digest, kind="scene_video", expected_state_version=first.state_version,
    )
    intent = evidence_service.create_upload_intent(
        str(actor), first.id, request, source_kind="insured_upload",
        deposit_grant_id=first_link.grant_id,
    )
    storage.objects[intent.storage_path] = StoredObjectInfo(
        byte_size=len(video_bytes), mime_type="video/mp4",
    )
    storage.object_bytes[intent.storage_path] = video_bytes

    if invalidation == "revoke":
        grants.revoke(first_link.grant_id, first.id, actor_id=actor)
    else:
        with engine.begin() as connection:
            connection.execute(text("""update public.deposit_grants
                set created_at = now() - interval '2 days', expires_at = now() - interval '1 second'
                where id = :id"""), {"id": first_link.grant_id})

    # Bypass the portal's earlier authorization. The repository must reject
    # invalidation that happened after the intent or while bytes were read.
    with pytest.raises(DepositGrantError, match=error_code):
        evidence_service.finalize_upload(str(actor), first.id, FinalizeEvidenceRequest(
            storage_path=intent.storage_path, client_sha256=digest, kind="scene_video",
            expected_state_version=first.state_version,
        ), verify_bytes=True)
    with pytest.raises(DepositGrantError, match=error_code):
        repository.register_upload_intent(
            actor, first.id, first.state_version, f"{first.id}/uploads/late.mp4",
            "scene_video", "video/mp4", len(video_bytes), digest,
            first_link.expires_at, "late.mp4", "insured_upload", first_link.grant_id,
        )
    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from public.evidence where case_id=:id"),
                                  {"id": first.id}).scalar_one() == 0
        assert connection.execute(text("""select finalized_evidence_id
            from public.evidence_upload_intents where storage_path=:path"""),
            {"path": intent.storage_path}).scalar_one_or_none() is None
        assert connection.execute(text("select content_revision from public.cases where id=:id"),
                                  {"id": first.id}).scalar_one() == first.content_revision


def test_g1_video_selection_is_idempotent_and_does_not_copy_vision_results(portal_db, monkeypatch):
    (database_url, engine, _actor, first, second, _grants, portal,
     _first_link, first_token, _second_link, second_token) = portal_db
    storage = FakeStorage()
    monkeypatch.setattr("claim_api.insured_portal.SupabaseStorageAdapter.from_env", lambda: storage)
    monkeypatch.setattr(portal, "_evidence_service", lambda: EvidenceService(PostgresCaseRepository(database_url), storage))
    first_session = portal.exchange(first_token)["session_token"]
    second_session = portal.exchange(second_token)["session_token"]
    catalogue = portal.demo_media(first_session)
    assert {item["id"] for item in catalogue} == {"photo-ensemble.png", "photo-detail.png", "video-g1.mp4"}
    first_result = portal.attach_demo(first_session, "video-g1.mp4", first.state_version)
    repeated = portal.attach_demo(first_session, "video-g1.mp4", first.state_version)
    assert repeated["content_revision"] == first_result["content_revision"] == first.content_revision + 1
    assert len(first_result["evidence"]) == 1
    assert first_result["evidence"][0]["source_kind"] == "demo_fixture"
    assert portal.summary(second_session)["evidence"] == []
    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from public.deposit_demo_media where case_id=:id"),
                                  {"id": first.id}).scalar_one() == 1
        assert connection.execute(text("select count(*) from public.case_media_analysis_links where case_id=:id"),
                                  {"id": first.id}).scalar_one() == 0
    assert portal.signed_read_url(first_session, first_result["evidence"][0]["id"])["url"]


@pytest.mark.parametrize("scenario", ["g2", "g3"])
def test_g2_g3_gallery_is_private_case_scoped_and_idempotent(portal_db, monkeypatch, scenario):
    (database_url, engine, actor, first, _second, _grants, portal,
     _first_link, first_token, _second_link, _second_token) = portal_db
    case = CaseService(PostgresCaseRepository(database_url)).create_case(str(actor), scenario)
    other = CaseService(PostgresCaseRepository(database_url)).create_case(str(actor), scenario)
    grants = DepositGrantService(database_url, "https://claim.example/depot")

    def make_session(target):
        issued = grants.issue(target.id, target.content_revision, timedelta(hours=2), actor_id=actor)
        with grants._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""insert into public.case_messages
                (case_id, recipient, body, provider, idempotency_key, payload_hash)
                values (%s,'+33100000000',%s,'mock',%s,%s) returning id""",
                (target.id, issued.url, str(uuid4()), "a" * 64))
            grants.associate(cursor, issued.grant_id, cursor.fetchone()["id"], target.id)
        return portal.exchange(issued.url.split("#token=", 1)[1])["session_token"]

    session = make_session(case)
    other_session = make_session(other)
    first_session = portal.exchange(first_token)["session_token"]
    storage = FakeStorage()
    monkeypatch.setattr("claim_api.insured_portal.SupabaseStorageAdapter.from_env", lambda: storage)
    monkeypatch.setattr(portal, "_evidence_service", lambda: EvidenceService(PostgresCaseRepository(database_url), storage))

    catalogue = portal.demo_media(session)
    expected = CATALOGUES[scenario]
    assert {item["id"] for item in catalogue} == {item.filename for item in expected.items}
    assert {item["provenance"] for item in catalogue} == {"demo_fixture"}
    assert all(item["thumbnail_key"] in {entry["id"] for entry in catalogue} for item in catalogue)
    assert all("prompt" not in str(item).lower() and "annotation" not in str(item).lower()
               for item in catalogue)
    video = next(item for item in catalogue if item["kind"] == "scene_video")
    assert video["duration_seconds"] == pytest.approx(5.042)
    if scenario == "g3":
        assert {item["subject"] for item in catalogue if item["kind"] != "scene_video"} == {"Toyota, véhicule tiers"}

    app = create_app()
    app.dependency_overrides[get_portal_service] = lambda: portal
    client = TestClient(app)
    thumbnail = client.get(f"/v1/deposit/demo-media/{video['thumbnail_key']}/preview",
                           headers={"Authorization": f"Bearer {session}"})
    assert thumbnail.status_code == 200
    assert thumbnail.content == (MEDIA_ROOT / scenario / video["thumbnail_key"]).read_bytes()
    assert thumbnail.headers["cache-control"] == "no-store"
    assert client.get(f"/v1/deposit/demo-media/{video['thumbnail_key']}/preview",
                      headers={"Authorization": f"Bearer {first_session}"}).status_code == 404

    for item in catalogue:
        data, mime = portal.demo_preview(session, item["id"])
        assert mime == item["mime_type"]
        assert data == (MEDIA_ROOT / scenario / item["id"]).read_bytes()
        with pytest.raises(EvidenceNotFoundError):
            portal.demo_preview(first_session, item["id"])
    with pytest.raises(EvidenceNotFoundError):
        portal.attach_demo(first_session, video["id"], first.state_version)
    with pytest.raises(EvidenceNotFoundError):
        portal.demo_preview(session, "../provenance.json")

    attached = portal.attach_demo(session, video["id"], case.state_version)
    repeated = portal.attach_demo(session, video["id"], case.state_version)
    other_attached = portal.attach_demo(other_session, video["id"], other.state_version)
    assert attached["content_revision"] == repeated["content_revision"] == case.content_revision + 1
    assert other_attached["content_revision"] == other.content_revision + 1
    assert portal.demo_media(session)[-1]["attached"]
    assert portal.summary(first_session)["evidence"] == []
    assert portal.signed_read_url(session, attached["evidence"][0]["id"])["url"]
    with engine.connect() as connection:
        rows = connection.execute(text("""select d.case_id, e.storage_path, e.sha256_verified
            from public.deposit_demo_media d join public.evidence e on e.id=d.evidence_id
            where d.case_id in (:first, :other)"""), {"first": case.id, "other": other.id}).all()
        assert len(rows) == 2
        assert rows[0].storage_path != rows[1].storage_path
        assert all(str(row.case_id) in row.storage_path for row in rows)
        assert all(row.sha256_verified == hashlib.sha256((MEDIA_ROOT / scenario / video["id"]).read_bytes()).hexdigest()
                   for row in rows)
        assert connection.execute(text("""select count(*) from public.case_media_analysis_links
            where case_id in (:first, :other)"""), {"first": case.id, "other": other.id}).scalar_one() == 0


@pytest.mark.parametrize("scenario", ["g2", "g3"])
def test_demo_manifest_matches_versioned_media_provenance(scenario):
    manifest = json.loads((MEDIA_ROOT / scenario / "provenance.json").read_text())
    catalogue = CATALOGUES[scenario]
    assert {item.filename for item in catalogue.items} == {
        manifest["source_video"]["file"], *(photo["file"] for photo in manifest["photos"]),
    }
    for entry in [manifest["source_video"], *manifest["photos"]]:
        data = (MEDIA_ROOT / scenario / entry["file"]).read_bytes()
        assert len(data) == entry["size_bytes"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert next(item for item in catalogue.items if item.kind == "scene_video").duration_seconds == pytest.approx(
        manifest["source_video"]["duration_seconds_rounded"])


def test_g1_retry_recovers_exact_fixture_left_by_interrupted_attach(portal_db, monkeypatch):
    (_url, _engine, _actor, first, _second, _grants, portal,
     _first_link, first_token, _second_link, _second_token) = portal_db
    storage = FakeStorage()
    path = f"{first.id}/deposit-demo/{G1_MEDIA_VERSION}/photo-detail.png"
    data = (G1_MEDIA_DIR / "photo-detail.png").read_bytes()
    storage.fixture_bytes[path] = data
    storage.objects[path] = StoredObjectInfo(byte_size=len(data), mime_type="image/png")
    def already_exists(_path, _data, _mime):
        raise StorageAdapterError("Already exists")
    storage.upload_fixture = already_exists
    monkeypatch.setattr("claim_api.insured_portal.SupabaseStorageAdapter.from_env", lambda: storage)
    session = portal.exchange(first_token)["session_token"]
    result = portal.attach_demo(session, "photo-detail.png", first.state_version)
    assert len(result["evidence"]) == 1


def test_demo_attach_rejects_storage_bytes_that_differ_from_versioned_fixture(portal_db, monkeypatch):
    (_url, engine, _actor, first, _second, _grants, portal,
     _first_link, first_token, _second_link, _second_token) = portal_db
    storage = FakeStorage()

    def corrupt_upload(path, data, mime):
        storage.fixture_bytes[path] = bytes([data[0] ^ 1]) + data[1:]
        storage.objects[path] = StoredObjectInfo(byte_size=len(data), mime_type=mime)

    storage.upload_fixture = corrupt_upload
    monkeypatch.setattr("claim_api.insured_portal.SupabaseStorageAdapter.from_env", lambda: storage)
    session = portal.exchange(first_token)["session_token"]
    with pytest.raises(DepositGrantError, match="invalid_upload"):
        portal.attach_demo(session, "photo-detail.png", first.state_version)
    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from public.deposit_demo_media where case_id=:id"),
                                  {"id": first.id}).scalar_one() == 0


def test_guest_api_uses_bearer_link_exchange_then_session_only(portal_db, monkeypatch):
    (_url, _engine, _actor, first, _second, _grants, portal,
     _first_link, first_token, _second_link, _second_token) = portal_db
    app = create_app()
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.example")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-only-manager-signing-key")
    app.dependency_overrides[get_portal_service] = lambda: portal
    client = TestClient(app)
    denied = client.get("/v1/deposit/summary", headers={"Authorization": f"Bearer {first_token}"})
    assert denied.status_code == 401
    exchanged = client.get("/v1/deposit/session", headers={"Authorization": f"Bearer {first_token}"})
    assert exchanged.status_code == 200
    assert exchanged.headers["cache-control"] == "no-store"
    assert exchanged.headers["referrer-policy"] == "no-referrer"
    session = exchanged.json()["session_token"]
    result = client.get("/v1/deposit/summary", headers={"Authorization": f"Bearer {session}"})
    assert result.status_code == 200 and result.json()["case_id"] == str(first.id)
    assert "storage_path" not in str(result.json())
    manager_route = client.get(f"/v1/cases/{first.id}", headers={"Authorization": f"Bearer {session}"})
    assert manager_route.status_code == 401


@pytest.mark.parametrize("final_status", ["registered", "sent"])
def test_insured_cannot_mutate_registered_or_sent_case(portal_db, monkeypatch, final_status):
    (database_url, engine, _actor, first, _second, _grants, portal,
     _first_link, first_token, _second_link, _second_token) = portal_db
    storage = FakeStorage()
    monkeypatch.setattr(portal, "_evidence_service", lambda: EvidenceService(PostgresCaseRepository(database_url), storage))
    session = portal.exchange(first_token)["session_token"]
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\0" * 504
    digest = hashlib.sha256(image_bytes).hexdigest()
    request = CreateEvidenceUploadIntentRequest(filename="scene.png", mime_type="image/png",
        byte_size=len(image_bytes), client_sha256=digest, kind="scene_photo",
        expected_state_version=first.state_version)
    intent = portal.create_upload_intent(session, request)
    storage.objects[intent.storage_path] = StoredObjectInfo(byte_size=len(image_bytes), mime_type="image/png")
    storage.object_bytes[intent.storage_path] = image_bytes
    with engine.begin() as connection:
        connection.execute(text("update public.cases set status=:status where id=:id"),
                           {"status": final_status, "id": first.id})
    with pytest.raises(CaseReadOnlyError):
        portal.correct(session, first.state_version, "location", "Paris", None)
    with pytest.raises(CaseReadOnlyError):
        portal.create_upload_intent(session, request)
    with pytest.raises(CaseReadOnlyError):
        portal.finalize_upload(session, FinalizeEvidenceRequest(storage_path=intent.storage_path,
            client_sha256=digest, kind="scene_photo", expected_state_version=first.state_version))
    with pytest.raises(CaseReadOnlyError):
        portal.attach_demo(session, "photo-detail.png", first.state_version)
    with engine.connect() as connection:
        case = connection.execute(text("select status, content_revision from public.cases where id=:id"),
                                  {"id": first.id}).one()
        assert case.status == final_status and case.content_revision == first.content_revision
        assert connection.execute(text("select count(*) from public.evidence where case_id=:id"),
                                  {"id": first.id}).scalar_one() == 0


def test_name_is_missing_until_confirmed_and_reused_on_reopening(portal_db):
    (_url, engine, actor, first, _second, grants, portal,
     _link, token, _link2, _token2) = portal_db
    with engine.begin() as connection:
        connection.execute(text("update public.cases set intake_json=jsonb_set(intake_json, '{insured_name}', 'null') where id=:id"), {"id": first.id})
    session = portal.exchange(token)["session_token"]
    summary = portal.summary(session)
    assert "insured_name" in summary["missing_fields"]
    updated = portal.correct(session, summary["state_version"], "insured_name", "Alex Martin", "Confirmed")
    assert updated["intake"]["insured_name"] == "Alex Martin"
    assert "insured_name" not in updated["missing_fields"]
    assert portal.summary(portal.exchange(token)["session_token"])["intake"]["insured_name"] == "Alex Martin"


def test_chat_history_survives_sessions_is_append_only_idempotent_and_case_scoped(portal_db):
    (_url, _engine, actor, first, _second, grants, portal,
     link, token, _link2, token2) = portal_db
    session = portal.exchange(token)["session_token"]
    other = portal.exchange(token2)["session_token"]
    state = {"phase": "collect", "opening": "Bonjour", "openingAt": "2026-09-27T10:00:00Z",
             "messages": [{"id": str(uuid4()), "role": "assistant", "text": "Votre nom ?",
                           "created_at": "2026-09-27T10:01:00Z"}], "proposal": None}
    assert portal.chat_history(session) == {"revision": 0, "state": None}
    saved = portal.save_chat_history(session, 0, state)
    assert saved["revision"] == 1
    assert portal.save_chat_history(session, 0, state) == saved
    reopened = portal.exchange(token)["session_token"]
    assert portal.chat_history(reopened) == saved
    assert portal.chat_history(other) == {"revision": 0, "state": None}
    updated = {**state, "messages": [*state["messages"], {"id": str(uuid4()), "role": "insured",
               "text": "Alex Martin", "created_at": "2026-09-27T10:02:00Z"}],
               "proposal": {"field": "insured_name", "value": "Alex Martin"}}
    assert portal.save_chat_history(reopened, 1, updated)["revision"] == 2
    with pytest.raises(DepositGrantError, match="stale_chat_history"):
        portal.save_chat_history(session, 1, {**state, "phase": "photos"})
    with pytest.raises(DepositGrantError, match="stale_chat_history"):
        portal.save_chat_history(session, 2, state)  # No truncation, even with a current revision.
    grants.revoke(link.grant_id, first.id, actor_id=actor)
    for action in (lambda: portal.chat_history(session), lambda: portal.save_chat_history(session, 2, updated)):
        with pytest.raises(DepositGrantError, match="revoked_grant"):
            action()


def test_history_routes_validate_state_and_are_private(portal_db):
    (_url, _engine, _actor, _first, _second, _grants, portal,
     _link, token, _link2, _token2) = portal_db
    app = create_app()
    app.dependency_overrides[get_portal_service] = lambda: portal
    client = TestClient(app)
    session = portal.exchange(token)["session_token"]
    headers = {"Authorization": f"Bearer {session}"}
    assert client.get('/v1/deposit/chat-history').status_code == 401
    state = {"phase": "collect", "opening": "Bonjour", "openingAt": "2026-09-27T10:00:00.000Z",
             "messages": [{"id": str(uuid4()), "role": "insured", "text": "Alex Martin",
                           "created_at": "2026-09-27T10:01:00.000Z"}],
             "proposal": {"field": "insured_name", "value": "Alex Martin"}}
    payload = {"expected_revision": 0, "state": state}
    saved = client.put('/v1/deposit/chat-history', headers=headers, json=payload)
    assert saved.status_code == 200
    assert saved.headers['cache-control'] == 'no-store'
    assert client.put('/v1/deposit/chat-history', headers=headers, json=payload).json() == saved.json()
    restored = client.get('/v1/deposit/chat-history', headers=headers)
    assert restored.json() == saved.json()
    assert restored.headers['referrer-policy'] == 'no-referrer'
    invalid = {**state, "messages": [state['messages'][0], state['messages'][0]]}
    assert client.put('/v1/deposit/chat-history', headers=headers,
                      json={"expected_revision": 1, "state": invalid}).status_code == 422
    assert portal.summary(session)['intake']['insured_name'] != 'Alex Martin'  # History is not a claim correction.
