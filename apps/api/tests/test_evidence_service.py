from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.request import Request
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.cctv_fixture import CCTV_FIXTURE_EVENT_ID
from claim_api.g1_media import G1_MEDIA, G1_MEDIA_DIR
from claim_api.evidence_service import (
    EvidenceNotFoundError,
    EvidenceService,
    InvalidEvidenceUploadError,
    UploadIntentRecord,
)
from claim_api.fixtures import SCENARIOS
from claim_api.main import create_app
from claim_api.models import (
    AuditEventView,
    CaseView,
    CreateEvidenceUploadIntentRequest,
    EvidenceReadUrlView,
    EvidenceView,
    FinalizeEvidenceRequest,
    ProviderResultView,
    ReceiveCCTVRequest,
    SeedG1MediaRequest,
)
from claim_api.routes.evidence import get_evidence_service
from claim_api.case_service import StaleCaseError
from claim_api.storage import (
    StorageObjectNotFound,
    StoredObjectInfo,
    SupabaseStorageAdapter,
    UploadSignature,
)


ACTOR = UUID("00000000-0000-4000-8000-000000000042")


class MemoryEvidenceRepository:
    def __init__(self, scenario_id: str = "complete") -> None:
        now = datetime(2025, 6, 14, 16, 1, tzinfo=timezone.utc)
        self.case = CaseView(
            id=uuid4(),
            created_by_user_id=ACTOR,
            scenario_id=scenario_id,
            synthetic=True,
            status="collecting",
            state_version=1,
            content_revision=1,
            created_at=now,
            updated_at=now,
            intake=SCENARIOS[scenario_id].intake,
            evidence=[],
            provider_results=[],
            timeline=[AuditEventView(
                id=uuid4(), actor_user_id=ACTOR, event_type="case.created",
                state_version_before=0, state_version_after=1,
                content_revision_before=0, content_revision_after=1,
                metadata={}, occurred_at=now,
            )],
        )
        self.intents: dict[str, UploadIntentRecord] = {}

    def case_state(self, actor_id: UUID, case_id: UUID):
        if actor_id != ACTOR or case_id != self.case.id:
            return None
        return self.case.state_version, self.case.scenario_id

    def register_upload_intent(self, actor_id, case_id, expected_state_version, storage_path, kind, mime_type, byte_size, client_sha256, expires_at, filename=None, source_kind="handler_upload", deposit_grant_id=None):
        if self.case_state(actor_id, case_id) is None:
            return False
        if self.case.state_version != expected_state_version:
            raise StaleCaseError(self.case.state_version)
        intent = UploadIntentRecord(
            id=uuid4(), case_id=case_id, created_by_user_id=actor_id,
            storage_path=storage_path, kind=kind, mime_type=mime_type,
            byte_size=byte_size, client_sha256=client_sha256,
            expires_at=expires_at, finalized_evidence_id=None,
        )
        self.intents[storage_path] = intent
        return True

    def get_upload_intent(self, actor_id, case_id, storage_path):
        intent = self.intents.get(storage_path)
        return intent if intent and intent.case_id == case_id and intent.created_by_user_id == actor_id else None

    def get_case(self, actor_id, case_id):
        return self.case if actor_id == ACTOR and case_id == self.case.id else None

    def finalize_evidence(self, actor_id, case_id, storage_path, expected_state_version):
        intent = self.get_upload_intent(actor_id, case_id, storage_path)
        if intent is None:
            return None
        if intent.finalized_evidence_id:
            return self.case
        if self.case.state_version != expected_state_version:
            raise StaleCaseError(self.case.state_version)
        now = datetime.now(timezone.utc)
        evidence = EvidenceView(
            id=uuid4(), case_id=case_id, kind=intent.kind, storage_path=storage_path,
            source_kind="handler_upload", mode="live", mime_type=intent.mime_type,
            byte_size=intent.byte_size, client_sha256=intent.client_sha256,
            checksum_status="client_declared", received_at=now,
        )
        event = AuditEventView(
            id=uuid4(), actor_user_id=actor_id, event_type="case.evidence_added",
            state_version_before=self.case.state_version, state_version_after=self.case.state_version + 1,
            content_revision_before=self.case.content_revision,
            content_revision_after=self.case.content_revision + 1,
            metadata={"evidence_id": str(evidence.id)}, occurred_at=now,
        )
        self.case = self.case.model_copy(update={
            "state_version": self.case.state_version + 1,
            "content_revision": self.case.content_revision + 1,
            "updated_at": now,
            "evidence": [*self.case.evidence, evidence],
            "timeline": [*self.case.timeline, event],
        })
        self.intents[storage_path] = UploadIntentRecord(
            **{**intent.__dict__, "finalized_evidence_id": evidence.id}
        )
        return self.case

    def get_owned_evidence(self, actor_id, case_id, evidence_id):
        if actor_id != ACTOR or case_id != self.case.id:
            return None
        return next((evidence for evidence in self.case.evidence if evidence.id == evidence_id), None)

    def receive_cctv_event(self, actor_id, case_id, fixture_event_id, expected_state_version, evidence_id, storage_path, byte_size, client_sha256, observation_payload, fixture_version, provider_version):
        if actor_id != ACTOR or case_id != self.case.id or self.case.scenario_id != "complete":
            return None
        if any(event.metadata.get("fixture_event_id") == fixture_event_id for event in self.case.timeline):
            return self.case
        if self.case.state_version != expected_state_version:
            raise StaleCaseError(self.case.state_version)
        now = datetime.now(timezone.utc)
        evidence = EvidenceView(
            id=evidence_id, case_id=case_id, kind="cctv_frame", storage_path=storage_path,
            source_kind="synthetic_cctv", mode="mock", mime_type="image/png",
            byte_size=byte_size, client_sha256=client_sha256, checksum_status="verified", received_at=now,
        )
        result_id = uuid4()
        result = ProviderResultView(
            id=result_id, source_id=result_id, provider="mock_vision", mode="mock",
            status="matched", source_version=provider_version,
            query_hash=hashlib.sha256(storage_path.encode()).hexdigest(), retrieved_at=now,
            data=observation_payload, reason="Fixed synthetic visual observations.",
        )
        event = AuditEventView(
            id=uuid4(), actor_user_id=actor_id, event_type="case.cctv_received",
            state_version_before=self.case.state_version, state_version_after=self.case.state_version + 1,
            content_revision_before=self.case.content_revision,
            content_revision_after=self.case.content_revision + 1,
            metadata={"fixture_event_id": fixture_event_id, "fixture_version": fixture_version},
            occurred_at=now,
        )
        self.case = self.case.model_copy(update={
            "state_version": self.case.state_version + 1,
            "content_revision": self.case.content_revision + 1,
            "updated_at": now,
            "evidence": [*self.case.evidence, evidence],
            "provider_results": [*self.case.provider_results, result],
            "timeline": [*self.case.timeline, event],
        })
        return self.case

    def seed_g1_media(self, actor_id, case_id, expected_state_version, media, fixture_version):
        if actor_id != ACTOR or case_id != self.case.id or self.case.scenario_id != "g1":
            return None
        if any(event.event_type == "case.g1_media_seeded" for event in self.case.timeline):
            return self.case
        if self.case.state_version != expected_state_version:
            raise StaleCaseError(self.case.state_version)
        now = datetime.now(timezone.utc)
        evidence = [EvidenceView(
            id=item["id"], case_id=case_id, kind=item["kind"], storage_path=item["storage_path"],
            source_kind="synthetic_g1", mode="mock", mime_type=item["mime_type"],
            byte_size=item["byte_size"], client_sha256=item["sha256"],
            checksum_status="verified", role=item["role"], display_order=item["display_order"], received_at=now,
        ) for item in media]
        event = AuditEventView(
            id=uuid4(), actor_user_id=actor_id, event_type="case.g1_media_seeded",
            state_version_before=self.case.state_version, state_version_after=self.case.state_version + 1,
            content_revision_before=self.case.content_revision,
            content_revision_after=self.case.content_revision + 1,
            metadata={"fixture_version": fixture_version}, occurred_at=now,
        )
        self.case = self.case.model_copy(update={
            "state_version": self.case.state_version + 1,
            "content_revision": self.case.content_revision + 1,
            "evidence": [*self.case.evidence, *evidence],
            "timeline": [*self.case.timeline, event],
        })
        return self.case


class FakeStorage:
    bucket = "claim-evidence"

    def __init__(self) -> None:
        self.objects: dict[str, StoredObjectInfo] = {}
        self.fixture_bytes: dict[str, bytes] = {}
        self.object_bytes: dict[str, bytes] = {}

    def create_signed_upload(self, storage_path: str) -> UploadSignature:
        return UploadSignature(token="opaque-one-time-token")

    def inspect_object(self, storage_path: str) -> StoredObjectInfo:
        try:
            return self.objects[storage_path]
        except KeyError as error:
            raise StorageObjectNotFound from error

    def create_signed_read_url(self, storage_path: str, expires_in: int) -> str:
        if storage_path not in self.objects and storage_path not in self.fixture_bytes:
            raise StorageObjectNotFound
        return f"https://project.example/storage/v1/object/sign/claim-evidence/{storage_path}?token=read-token"

    def upload_fixture(self, storage_path: str, data: bytes, mime_type: str) -> None:
        self.fixture_bytes[storage_path] = data
        self.objects[storage_path] = StoredObjectInfo(byte_size=len(data), mime_type=mime_type)

    def read_object_chunks(self, storage_path: str):
        try:
            yield self.object_bytes[storage_path] if storage_path in self.object_bytes else self.fixture_bytes[storage_path]
        except KeyError as error:
            raise StorageObjectNotFound from error


def _upload_request(case: CaseView, **overrides) -> CreateEvidenceUploadIntentRequest:
    values = {
        "filename": "scene.png",
        "mime_type": "image/png",
        "byte_size": 512,
        "client_sha256": "a" * 64,
        "kind": "scene_photo",
        "expected_state_version": case.state_version,
    }
    values.update(overrides)
    return CreateEvidenceUploadIntentRequest(**values)


def test_signed_upload_is_case_scoped_verified_and_single_use() -> None:
    repository = MemoryEvidenceRepository()
    storage = FakeStorage()
    service = EvidenceService(repository, storage)

    intent = service.create_upload_intent(str(ACTOR), repository.case.id, _upload_request(repository.case))

    assert intent.storage_path.startswith(f"{repository.case.id}/uploads/")
    assert intent.bucket == "claim-evidence"
    assert intent.token == "opaque-one-time-token"
    assert intent.expires_at > datetime.now(timezone.utc)

    storage.objects[intent.storage_path] = StoredObjectInfo(byte_size=512, mime_type="image/png")
    finalized = service.finalize_upload(str(ACTOR), repository.case.id, FinalizeEvidenceRequest(
        storage_path=intent.storage_path,
        client_sha256="a" * 64,
        kind="scene_photo",
        expected_state_version=1,
    ))
    assert finalized.state_version == 2
    assert finalized.content_revision == 2
    assert finalized.evidence[0].checksum_status == "client_declared"
    assert finalized.evidence[0].source_kind == "handler_upload"
    assert finalized.timeline[-1].event_type == "case.evidence_added"

    retry = service.finalize_upload(str(ACTOR), repository.case.id, FinalizeEvidenceRequest(
        storage_path=intent.storage_path,
        client_sha256="a" * 64,
        kind="scene_photo",
        expected_state_version=1,
    ))
    assert retry.state_version == 2
    assert len(retry.evidence) == 1


def test_upload_finalization_rejects_foreign_path_metadata_and_storage_mismatch() -> None:
    repository = MemoryEvidenceRepository()
    storage = FakeStorage()
    service = EvidenceService(repository, storage)
    intent = service.create_upload_intent(str(ACTOR), repository.case.id, _upload_request(repository.case))

    with pytest.raises(EvidenceNotFoundError):
        service.finalize_upload(str(ACTOR), repository.case.id, FinalizeEvidenceRequest(
            storage_path=f"{uuid4()}/uploads/x.png", client_sha256="a" * 64,
            kind="scene_photo", expected_state_version=1,
        ))
    with pytest.raises(InvalidEvidenceUploadError, match="match the issued"):
        service.finalize_upload(str(ACTOR), repository.case.id, FinalizeEvidenceRequest(
            storage_path=intent.storage_path, client_sha256="b" * 64,
            kind="scene_photo", expected_state_version=1,
        ))

    storage.objects[intent.storage_path] = StoredObjectInfo(byte_size=500, mime_type="image/png")
    with pytest.raises(InvalidEvidenceUploadError, match="size"):
        service.finalize_upload(str(ACTOR), repository.case.id, FinalizeEvidenceRequest(
            storage_path=intent.storage_path, client_sha256="a" * 64,
            kind="scene_photo", expected_state_version=1,
        ))


def test_video_upload_requires_matching_kind_size_and_object_before_finalization() -> None:
    repository = MemoryEvidenceRepository("g1")
    storage = FakeStorage()
    service = EvidenceService(repository, storage)
    for mime, kind, size in [
        ("video/mp4", "document", 512),
        ("image/png", "scene_video", 512),
        ("image/png", "scene_photo", 5_242_881),
    ]:
        with pytest.raises(InvalidEvidenceUploadError):
            service.create_upload_intent(str(ACTOR), repository.case.id,
                _upload_request(repository.case, mime_type=mime, kind=kind, byte_size=size))
    with pytest.raises(ValidationError):
        _upload_request(repository.case, mime_type="video/mp4", kind="scene_video", byte_size=52_428_801)

    intent = service.create_upload_intent(str(ACTOR), repository.case.id,
        _upload_request(repository.case, mime_type="video/mp4", kind="scene_video", byte_size=8_000_000))
    assert intent.storage_path.endswith(".mp4")
    finalize = FinalizeEvidenceRequest(storage_path=intent.storage_path, client_sha256="a" * 64,
        kind="scene_video", expected_state_version=1)
    with pytest.raises(InvalidEvidenceUploadError, match="not present"):
        service.finalize_upload(str(ACTOR), repository.case.id, finalize)
    assert repository.case.evidence == []
    storage.objects[intent.storage_path] = StoredObjectInfo(byte_size=8_000_000, mime_type="image/png")
    with pytest.raises(InvalidEvidenceUploadError, match="type"):
        service.finalize_upload(str(ACTOR), repository.case.id, finalize)
    storage.objects[intent.storage_path] = StoredObjectInfo(byte_size=8_000_000, mime_type="video/mp4")
    with pytest.raises(EvidenceNotFoundError):
        service.finalize_upload(str(uuid4()), repository.case.id, finalize)
    with pytest.raises(EvidenceNotFoundError):
        service.signed_read_url(str(ACTOR), uuid4(), uuid4())
    result = service.finalize_upload(str(ACTOR), repository.case.id, finalize)
    assert result.evidence[0].kind == "scene_video"
    assert result.evidence[0].role is None
    assert result.evidence[0].checksum_status == "client_declared"
    assert result.content_revision == 2
    with pytest.raises(EvidenceNotFoundError):
        service.signed_read_url(str(ACTOR), uuid4(), result.evidence[0].id)
    with pytest.raises(EvidenceNotFoundError):
        service.signed_read_url(str(uuid4()), repository.case.id, result.evidence[0].id)


def test_cctv_event_stores_deterministic_mock_media_and_observations() -> None:
    repository = MemoryEvidenceRepository()
    storage = FakeStorage()
    service = EvidenceService(repository, storage)

    result = service.receive_cctv(str(ACTOR), repository.case.id, ReceiveCCTVRequest(
        fixture_event_id=CCTV_FIXTURE_EVENT_ID,
        expected_state_version=1,
    ))

    evidence = result.evidence[0]
    vision = next(item for item in result.provider_results if item.provider == "mock_vision")
    assert result.state_version == 2 and result.content_revision == 2
    assert evidence.mode == "mock" and evidence.checksum_status == "verified"
    assert evidence.storage_path.startswith(f"{repository.case.id}/synthetic-cctv/")
    assert storage.fixture_bytes[evidence.storage_path].startswith(b"\x89PNG\r\n\x1a\n")
    assert vision.mode == "mock" and vision.source_id == vision.id
    first_observation = vision.data["observations"][0]
    assert first_observation["source_refs"][0]["id"] == str(evidence.id)
    assert result.timeline[-1].metadata["fixture_version"]

    duplicate = service.receive_cctv(str(ACTOR), repository.case.id, ReceiveCCTVRequest(
        fixture_event_id=CCTV_FIXTURE_EVENT_ID,
        expected_state_version=2,
    ))
    assert duplicate.state_version == 2
    assert len(duplicate.evidence) == 1
    assert len([item for item in duplicate.provider_results if item.provider == "mock_vision"]) == 1


def test_supabase_storage_adapter_uses_service_credential_only_server_side() -> None:
    calls: list[Request] = []

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.body = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            return self.body

    def fake_open(request: Request, timeout: int):
        assert timeout == 8
        calls.append(request)
        if "/upload/sign/" in request.full_url:
            return Response({"url": "/storage/v1/object/upload/sign/claim-evidence/case/x.png?token=upload-only"})
        if "/object/info/" in request.full_url:
            return Response({"size": 512, "contentType": "image/png"})
        return Response({"signedURL": "/object/sign/claim-evidence/case/x.png?token=read-only"})

    adapter = SupabaseStorageAdapter(
        "https://project.example", "server-only-secret", open_url=fake_open
    )
    signed = adapter.create_signed_upload("case/uploads/x.png")
    info = adapter.inspect_object("case/uploads/x.png")
    read_url = adapter.create_signed_read_url("case/uploads/x.png", 300)

    assert signed.token == "upload-only"
    assert info == StoredObjectInfo(byte_size=512, mime_type="image/png")
    assert read_url == "https://project.example/storage/v1/object/sign/claim-evidence/case/x.png?token=read-only"
    assert all(request.get_header("Authorization") == "Bearer server-only-secret" for request in calls)
    assert calls[0].get_header("X-upsert") == "false"


def test_evidence_api_upload_finalize_and_signed_read_url() -> None:
    repository = MemoryEvidenceRepository()
    storage = FakeStorage()
    service = EvidenceService(repository, storage)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(ACTOR))
    app.dependency_overrides[get_evidence_service] = lambda: service
    client = TestClient(app)
    case_id = repository.case.id

    intent_response = client.post(f"/v1/cases/{case_id}/evidence/upload-intents", json={
        "filename": "scene.png",
        "mime_type": "image/png",
        "byte_size": 512,
        "client_sha256": "a" * 64,
        "kind": "scene_photo",
        "expected_state_version": 1,
    })
    assert intent_response.status_code == 201
    intent = intent_response.json()
    storage.objects[intent["storage_path"]] = StoredObjectInfo(byte_size=512, mime_type="image/png")

    finalized = client.post(f"/v1/cases/{case_id}/evidence", json={
        "storage_path": intent["storage_path"],
        "client_sha256": "a" * 64,
        "kind": "scene_photo",
        "expected_state_version": 1,
    })
    assert finalized.status_code == 201
    evidence = finalized.json()["evidence"][0]
    read = client.get(f"/v1/cases/{case_id}/evidence/{evidence['id']}/read-url")
    assert read.status_code == 200
    assert read.json()["url"].startswith("https://project.example/")
    assert "url" not in finalized.json()["evidence"][0]


def test_g1_media_seed_is_private_verified_idempotent_and_case_scoped() -> None:
    repository = MemoryEvidenceRepository("g1")
    storage = FakeStorage()
    service = EvidenceService(repository, storage)
    case_id = repository.case.id

    with pytest.raises(StaleCaseError):
        service.seed_g1_media(str(ACTOR), case_id, SeedG1MediaRequest(expected_state_version=2))
    assert storage.objects == {}

    seeded = service.seed_g1_media(str(ACTOR), case_id, SeedG1MediaRequest(expected_state_version=1))
    assert seeded.state_version == 2
    assert seeded.content_revision == 2
    assert len(seeded.evidence) == len(G1_MEDIA) == 3
    for fixture, evidence in zip(G1_MEDIA, seeded.evidence, strict=True):
        data = (G1_MEDIA_DIR / fixture.filename).read_bytes()
        assert evidence.storage_path == f"{case_id}/synthetic-g1/{fixture.filename}"
        assert evidence.mime_type == fixture.mime_type
        assert evidence.byte_size == len(data)
        assert evidence.client_sha256 == hashlib.sha256(data).hexdigest()
        assert evidence.checksum_status == "verified"
        assert evidence.role == fixture.role
        assert evidence.display_order == fixture.display_order
        assert storage.fixture_bytes[evidence.storage_path] == data
        assert service.signed_read_url(str(ACTOR), case_id, evidence.id).url.startswith("https://project.example/")
    assert seeded.timeline[-1].event_type == "case.g1_media_seeded"

    duplicate = service.seed_g1_media(str(ACTOR), case_id, SeedG1MediaRequest(expected_state_version=1))
    assert duplicate.state_version == 2
    assert len(duplicate.evidence) == 3
    assert [(item.id, item.role, item.client_sha256) for item in duplicate.evidence] == [
        (item.id, item.role, item.client_sha256) for item in seeded.evidence
    ]
    with pytest.raises(EvidenceNotFoundError):
        service.signed_read_url(str(uuid4()), case_id, seeded.evidence[0].id)

    replacement_intent = service.create_upload_intent(str(ACTOR), case_id,
        _upload_request(duplicate, filename="replacement.mp4", mime_type="video/mp4",
            kind="scene_video", byte_size=1000))
    storage.objects[replacement_intent.storage_path] = StoredObjectInfo(byte_size=1000, mime_type="video/mp4")
    replaced = service.finalize_upload(str(ACTOR), case_id, FinalizeEvidenceRequest(
        storage_path=replacement_intent.storage_path, client_sha256="a" * 64,
        kind="scene_video", expected_state_version=duplicate.state_version))
    assert replaced.content_revision == duplicate.content_revision + 1
    assert len(replaced.evidence) == 4
    assert replaced.evidence[-1].source_kind == "handler_upload"
    assert replaced.evidence[-1].role is None
    assert [(item.id, item.role) for item in replaced.evidence[:3]] == [
        (item.id, item.role) for item in seeded.evidence
    ]


def test_g1_media_api_rejects_other_scenarios() -> None:
    repository = MemoryEvidenceRepository("complete")
    service = EvidenceService(repository, FakeStorage())
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(ACTOR))
    app.dependency_overrides[get_evidence_service] = lambda: service
    response = TestClient(app).post(
        f"/v1/cases/{repository.case.id}/demo-events/g1-media",
        json={"expected_state_version": 1},
    )
    assert response.status_code == 422
    assert repository.case.evidence == []
