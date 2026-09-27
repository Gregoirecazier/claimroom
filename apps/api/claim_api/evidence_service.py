from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Callable, Protocol
from uuid import UUID, uuid4

from claim_api.case_service import CaseNotFoundError, StaleCaseError, UnsupportedActorError
from claim_api.cctv_fixture import (
    CCTV_FIXTURE_EVENT_ID,
    CCTV_FIXTURE_VERSION,
    MOCK_VISION_VERSION,
    synthetic_cctv_png,
    synthetic_observations,
)
from claim_api.g1_media import G1_MEDIA, G1_MEDIA_DIR, G1_MEDIA_VERSION
from claim_api.models import (
    CaseView,
    CreateEvidenceUploadIntentRequest,
    EvidenceReadUrlView,
    EvidenceUploadIntentView,
    EvidenceView,
    FinalizeEvidenceRequest,
    ReceiveCCTVRequest,
    SeedG1MediaRequest,
)
from claim_api.storage import (
    StorageAdapter,
    StorageAdapterError,
    StorageObjectNotFound,
    StorageUnavailableError,
)


MAX_UPLOAD_BYTES = 5_242_880
MAX_VIDEO_UPLOAD_BYTES = 52_428_800
READ_URL_TTL_SECONDS = 300
ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "application/pdf", "video/mp4", "video/quicktime", "video/webm"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EvidenceNotFoundError(LookupError):
    pass


class InvalidEvidenceUploadError(ValueError):
    pass


class UploadIntentExpiredError(ValueError):
    pass


class CCTVFixtureUnavailableError(ValueError):
    pass


class G1MediaUnavailableError(ValueError):
    pass


@dataclass(frozen=True)
class UploadIntentRecord:
    id: UUID
    case_id: UUID
    created_by_user_id: UUID
    storage_path: str
    kind: str
    mime_type: str
    byte_size: int
    client_sha256: str
    expires_at: datetime
    finalized_evidence_id: UUID | None
    deposit_grant_id: UUID | None = None


class EvidenceRepository(Protocol):
    def case_state(self, actor_id: UUID, case_id: UUID) -> tuple[int, str] | None: ...

    def register_upload_intent(
        self,
        actor_id: UUID,
        case_id: UUID,
        expected_state_version: int,
        storage_path: str,
        kind: str,
        mime_type: str,
        byte_size: int,
        client_sha256: str,
        expires_at: datetime,
        filename: str | None = None,
        source_kind: str = "handler_upload",
        deposit_grant_id: UUID | None = None,
    ) -> bool: ...

    def get_upload_intent(
        self, actor_id: UUID, case_id: UUID, storage_path: str
    ) -> UploadIntentRecord | None: ...

    def finalize_evidence(
        self,
        actor_id: UUID,
        case_id: UUID,
        storage_path: str,
        expected_state_version: int,
        sha256_verified: str | None = None,
    ) -> CaseView | None: ...

    def get_owned_evidence(self, actor_id: UUID, case_id: UUID, evidence_id: UUID) -> EvidenceView | None: ...

    def get_case(self, actor_id: UUID, case_id: UUID) -> CaseView | None: ...

    def receive_cctv_event(
        self,
        actor_id: UUID,
        case_id: UUID,
        fixture_event_id: str,
        expected_state_version: int,
        evidence_id: UUID,
        storage_path: str,
        byte_size: int,
        client_sha256: str,
        observation_payload: dict[str, object],
        fixture_version: str,
        provider_version: str,
        camera_request_id: UUID | None = None,
    ) -> CaseView | None: ...

    def seed_g1_media(
        self, actor_id: UUID, case_id: UUID, expected_state_version: int,
        media: list[dict[str, object]], fixture_version: str,
    ) -> CaseView | None: ...


def actor_uuid(actor_id: str) -> UUID:
    try:
        return UUID(actor_id)
    except ValueError as error:
        raise UnsupportedActorError("Evidence storage requires a Supabase user UUID.") from error


class EvidenceService:
    def __init__(self, repository: EvidenceRepository, storage: StorageAdapter, max_upload_bytes: int = MAX_UPLOAD_BYTES,
                 *, on_media_received: Callable[[str, CaseView], CaseView] | None = None) -> None:
        self.repository = repository
        self.storage = storage
        self.max_upload_bytes = max_upload_bytes
        self.on_media_received = on_media_received

    def create_upload_intent(
        self, actor_id: str, case_id: UUID, request: CreateEvidenceUploadIntentRequest,
        *, source_kind: str = "handler_upload",
        deposit_grant_id: UUID | None = None,
    ) -> EvidenceUploadIntentView:
        actor = actor_uuid(actor_id)
        if request.mime_type not in ALLOWED_MIME_TYPES:
            raise InvalidEvidenceUploadError("This file type is not allowed.")
        is_video = request.mime_type.startswith("video/")
        if is_video != (request.kind in {"scene_video", "cctv_video", "insured_video"}):
            raise InvalidEvidenceUploadError("Videos require the CCTV video or insured video category.")
        limit = MAX_VIDEO_UPLOAD_BYTES if is_video else self.max_upload_bytes
        if request.byte_size > limit:
            raise InvalidEvidenceUploadError(f"Files must be {limit // 1_048_576} MB or smaller.")
        if not _SHA256_PATTERN.fullmatch(request.client_sha256):
            raise InvalidEvidenceUploadError("Provide a lowercase SHA-256 checksum for the selected file.")

        state = self.repository.case_state(actor, case_id)
        if state is None:
            raise CaseNotFoundError
        if state[0] != request.expected_state_version:
            raise StaleCaseError(state[0])

        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "application/pdf": ".pdf",
            "video/mp4": ".mp4",
            "video/quicktime": ".mov",
            "video/webm": ".webm",
        }[request.mime_type]
        # Do not use the client filename in the object key. The path is scoped
        # to the authorized case and cannot be chosen by the browser.
        storage_path = f"{case_id}/uploads/{uuid4()}{extension}"
        expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
        try:
            signature = self.storage.create_signed_upload(storage_path)
        except (StorageUnavailableError, StorageAdapterError):
            raise
        registered = self.repository.register_upload_intent(
            actor,
            case_id,
            request.expected_state_version,
            storage_path,
            request.kind,
            request.mime_type,
            request.byte_size,
            request.client_sha256,
            expires_at,
            request.filename,
            source_kind,
            deposit_grant_id,
        )
        if not registered:
            raise CaseNotFoundError
        return EvidenceUploadIntentView(
            bucket=self.storage.bucket,
            storage_path=storage_path,
            token=signature.token,
            mime_type=request.mime_type,
            byte_size=request.byte_size,
            expires_at=expires_at,
        )

    def finalize_upload(
        self, actor_id: str, case_id: UUID, request: FinalizeEvidenceRequest,
        *, verify_bytes: bool = False,
    ) -> CaseView:
        actor = actor_uuid(actor_id)
        if not request.storage_path.startswith(f"{case_id}/") or any(
            segment in {".", ".."} for segment in PurePosixPath(request.storage_path).parts
        ):
            raise EvidenceNotFoundError
        intent = self.repository.get_upload_intent(actor, case_id, request.storage_path)
        if intent is None:
            raise EvidenceNotFoundError
        if request.kind != intent.kind or request.client_sha256 != intent.client_sha256:
            raise InvalidEvidenceUploadError("Finalization metadata must match the issued upload intent.")
        if intent.expires_at <= datetime.now(timezone.utc) and intent.finalized_evidence_id is None:
            raise UploadIntentExpiredError("The upload intent expired; request a new upload URL.")

        if intent.finalized_evidence_id is not None:
            current = self.repository.get_case(actor, case_id)
            if current is None:
                raise EvidenceNotFoundError
            return current

        try:
            stored = self.storage.inspect_object(intent.storage_path)
        except StorageObjectNotFound as error:
            raise InvalidEvidenceUploadError("The uploaded object is not present in private Storage.") from error
        if stored.byte_size != intent.byte_size:
            raise InvalidEvidenceUploadError("Uploaded file size does not match the signed upload intent.")
        if stored.mime_type != intent.mime_type:
            raise InvalidEvidenceUploadError("Uploaded file type does not match the signed upload intent.")
        if intent.mime_type.startswith("video/") and (intent.kind not in {"scene_video", "cctv_video", "insured_video"} or stored.byte_size > MAX_VIDEO_UPLOAD_BYTES):
            raise InvalidEvidenceUploadError("Uploaded video does not match the video upload policy.")

        verified_digest = None
        if verify_bytes:
            digest = hashlib.sha256()
            prefix = b""
            total = 0
            try:
                for chunk in self.storage.read_object_chunks(intent.storage_path):
                    total += len(chunk)
                    if total > intent.byte_size:
                        raise InvalidEvidenceUploadError("Uploaded file size changed.")
                    if len(prefix) < 32:
                        prefix += chunk[:32 - len(prefix)]
                    digest.update(chunk)
            except StorageObjectNotFound as error:
                raise InvalidEvidenceUploadError("The uploaded object is not present in private Storage.") from error
            if total != intent.byte_size or digest.hexdigest() != intent.client_sha256:
                raise InvalidEvidenceUploadError("Uploaded file checksum does not match the selected file.")
            signatures = {
                "image/jpeg": prefix.startswith(b"\xff\xd8\xff"),
                "image/png": prefix.startswith(b"\x89PNG\r\n\x1a\n"),
                "image/webp": prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP",
                "application/pdf": prefix.startswith(b"%PDF-"),
                "video/mp4": prefix[4:8] == b"ftyp",
                "video/quicktime": prefix[4:8] == b"ftyp",
                "video/webm": prefix.startswith(b"\x1a\x45\xdf\xa3"),
            }
            if not signatures[intent.mime_type]:
                raise InvalidEvidenceUploadError("Uploaded file content does not match its type.")
            verified_digest = digest.hexdigest()

        if verify_bytes:
            result = self.repository.finalize_evidence(
                actor, case_id, intent.storage_path, request.expected_state_version, verified_digest
            )
        else:
            result = self.repository.finalize_evidence(
                actor, case_id, intent.storage_path, request.expected_state_version
            )
        if result is None:
            raise EvidenceNotFoundError
        if intent.mime_type.startswith(("video/", "image/")) and self.on_media_received:
            return self.on_media_received(actor_id, result)
        return result

    def signed_read_url(self, actor_id: str, case_id: UUID, evidence_id: UUID) -> EvidenceReadUrlView:
        actor = actor_uuid(actor_id)
        evidence = self.repository.get_owned_evidence(actor, case_id, evidence_id)
        if evidence is None:
            raise EvidenceNotFoundError
        try:
            signed_url = self.storage.create_signed_read_url(evidence.storage_path, READ_URL_TTL_SECONDS)
        except StorageObjectNotFound as error:
            raise EvidenceNotFoundError from error
        return EvidenceReadUrlView(
            evidence_id=evidence_id,
            url=signed_url,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=READ_URL_TTL_SECONDS),
        )

    def seed_g1_media(self, actor_id: str, case_id: UUID, request: SeedG1MediaRequest) -> CaseView:
        actor = actor_uuid(actor_id)
        current = self.repository.get_case(actor, case_id)
        if current is None:
            raise CaseNotFoundError
        if current.scenario_id != "g1":
            raise G1MediaUnavailableError("G1 media can only be added to a G1 case.")
        if any(event.event_type == "case.g1_media_seeded" for event in current.timeline):
            return current
        if current.state_version != request.expected_state_version:
            raise StaleCaseError(current.state_version)

        media_rows: list[dict[str, object]] = []
        for item in G1_MEDIA:
            try:
                data = (G1_MEDIA_DIR / item.filename).read_bytes()
            except OSError as error:
                raise G1MediaUnavailableError(f"Bundled G1 media is unavailable: {item.filename}.") from error
            storage_path = f"{case_id}/synthetic-g1/{item.filename}"
            self.storage.upload_fixture(storage_path, data, item.mime_type)
            stored = self.storage.inspect_object(storage_path)
            if stored.byte_size != len(data) or stored.mime_type != item.mime_type:
                raise StorageAdapterError("G1 media metadata did not match the uploaded fixture.")
            media_rows.append({
                "id": uuid4(), "storage_path": storage_path, "kind": item.kind,
                "mime_type": item.mime_type, "byte_size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "role": item.role, "display_order": item.display_order,
            })

        updated = self.repository.seed_g1_media(actor, case_id, request.expected_state_version, media_rows, G1_MEDIA_VERSION)
        if updated is None:
            raise CaseNotFoundError
        return self.on_media_received(actor_id, updated) if self.on_media_received else updated

    def receive_cctv(self, actor_id: str, case_id: UUID, request: ReceiveCCTVRequest) -> CaseView:
        actor = actor_uuid(actor_id)
        if request.fixture_event_id != CCTV_FIXTURE_EVENT_ID:
            raise CCTVFixtureUnavailableError("That synthetic CCTV fixture is not available.")
        current = self.repository.get_case(actor, case_id)
        if current is None:
            raise CaseNotFoundError
        if current.scenario_id != "complete":
            raise CCTVFixtureUnavailableError("The CCTV fixture is only available for the complete scenario.")

        already_received = any(
            event.event_type == "case.cctv_received"
            and event.metadata.get("fixture_event_id") == request.fixture_event_id
            for event in current.timeline
        )
        if already_received:
            return current
        if current.state_version != request.expected_state_version:
            raise StaleCaseError(current.state_version)
        if request.camera_request_id is not None and not any(
            item.id == request.camera_request_id and item.fixture_event_id == request.fixture_event_id
            and item.status in {"requested", "waiting"}
            for item in current.camera_requests
        ):
            raise CCTVFixtureUnavailableError("An approved simulated request is required for this CCTV fixture.")
        if request.camera_request_id is not None:
            from claim_api.camera_locator import MockCameraLocator
            if not any(item.fixture_event_id == request.fixture_event_id
                       for item in MockCameraLocator().search(current).candidates):
                raise CCTVFixtureUnavailableError("The camera fixture no longer matches the current intake.")

        fixture_bytes = synthetic_cctv_png()
        storage_path = f"{case_id}/synthetic-cctv/{request.fixture_event_id}.png"
        evidence_id = uuid4()
        try:
            self.storage.upload_fixture(storage_path, fixture_bytes, "image/png")
        except (StorageUnavailableError, StorageAdapterError):
            raise
        result = self.repository.receive_cctv_event(
            actor,
            case_id,
            request.fixture_event_id,
            request.expected_state_version,
            evidence_id,
            storage_path,
            len(fixture_bytes),
            hashlib.sha256(fixture_bytes).hexdigest(),
            synthetic_observations(str(evidence_id)),
            CCTV_FIXTURE_VERSION,
            MOCK_VISION_VERSION,
            **({"camera_request_id": request.camera_request_id} if request.camera_request_id is not None else {}),
        )
        if result is None:
            raise CaseNotFoundError
        return self.on_media_received(actor_id, result) if self.on_media_received else result
