from __future__ import annotations

import hashlib
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request
from uuid import UUID, uuid4

from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.storage import StorageAdapterError, StoredObjectInfo, SupabaseStorageAdapter
from claim_api.voice_recording import OriginalRecording, VapiRecordingFetcher, RecordingError, capture_original_recording
from claim_api.voice_repository import VoiceIntakeRepository

from test_vapi_native import _configure, _db_url, _event


def _wav() -> bytes:
    header = bytearray(44)
    header[:4] = b"RIFF"
    header[8:12] = b"WAVE"
    header[20:22] = (1).to_bytes(2, "little")
    header[22:24] = (2).to_bytes(2, "little")
    return bytes(header) + b"\x01\x02\x03\x04"


def test_fetcher_uses_authenticated_fixed_vapi_path_and_preserves_wav_bytes() -> None:
    media = _wav()
    seen: list[tuple[str, str | None, str | None]] = []

    def open_url(request: Request, timeout: int):
        seen.append((request.full_url, request.get_header("Authorization"), request.get_header("User-agent")))
        if len(seen) == 1:
            raise HTTPError(request.full_url, 302, "redirect",
                            {"Location": "https://media.example.test/signed?token=secret"}, None)
        return BytesIO(media)

    result = VapiRecordingFetcher("private-test-key", {"media.example.test"}, open_url).fetch("call-123")
    assert result.data == media and result.sha256 == hashlib.sha256(media).hexdigest()
    assert result.mime_type == "audio/wav"
    assert seen == [("https://api.vapi.ai/call/call-123/stereo-recording", "Bearer private-test-key", "Claimroom/1.0"),
                    ("https://media.example.test/signed?token=secret", None, None)]


def test_fetcher_rejects_untrusted_redirect() -> None:
    def open_url(request: Request, timeout: int):
        raise HTTPError(request.full_url, 302, "redirect", {"Location": "http://localhost/private"}, None)

    try:
        VapiRecordingFetcher("private-test-key", {"media.example.test"}, open_url).fetch("call-123")
    except RecordingError as error:
        assert error.code == "invalid_recording_redirect"
    else:
        raise AssertionError("untrusted redirect accepted")


def test_retry_accepts_only_identical_previously_uploaded_original() -> None:
    media = _wav()
    digest = hashlib.sha256(media).hexdigest()
    case_id = uuid4()
    actor = uuid4()

    class FakeRepository:
        def __init__(self) -> None:
            self.updates = []

        def get_state(self, actor_id, call_id):
            assert actor_id == actor and call_id == "call-123"
            return {"case_id": case_id, "recording_status": "pending"}

        def update_recording(self, actor_id, call_id, **values):
            self.updates.append(values)

    class FakeStorage:
        def __init__(self, existing: bytes) -> None:
            self.existing = existing

        def upload_private_object(self, path, data, mime_type):
            raise StorageAdapterError("Object already exists")

        def inspect_object(self, path):
            return StoredObjectInfo(byte_size=len(self.existing), mime_type="audio/wav")

        def read_object_chunks(self, path):
            yield self.existing

    class FakeFetcher:
        def fetch(self, call_id):
            return OriginalRecording(media, "audio/wav", "wav", digest)

    repository = FakeRepository()
    capture_original_recording(repository, actor, "call-123", FakeStorage(media), FakeFetcher())
    assert repository.updates[-1]["status"] == "available"
    assert repository.updates[-1]["sha256"] == digest

    repository = FakeRepository()
    capture_original_recording(repository, actor, "call-123", FakeStorage(media[:-1] + b"X"), FakeFetcher())
    assert repository.updates[-1]["status"] == "error"
    assert repository.updates[-1]["error_code"] == "recording_conflict"


def test_native_report_captures_original_once_and_read_url_is_owner_only(monkeypatch) -> None:
    database_url = _db_url()
    client = _configure(monkeypatch, database_url)
    media = _wav()
    uploads: list[tuple[str, bytes, str]] = []

    class FakeStorage:
        def upload_private_object(self, path: str, data: bytes, mime_type: str) -> None:
            uploads.append((path, data, mime_type))

        def create_signed_read_url(self, path: str, expires_in: int) -> str:
            assert expires_in == 300 and path == uploads[0][0]
            return "https://storage.example.test/short-lived"

    class FakeFetcher:
        def fetch(self, call_id: str):
            from claim_api.voice_recording import OriginalRecording
            return OriginalRecording(media, "audio/wav", "wav", hashlib.sha256(media).hexdigest())

    monkeypatch.setattr(SupabaseStorageAdapter, "from_env", classmethod(lambda cls, bucket=None: FakeStorage()))
    monkeypatch.setattr(VapiRecordingFetcher, "from_env", classmethod(lambda cls: FakeFetcher()))
    call_id = f"vapi-{uuid4()}"
    headers = {"X-Vapi-Secret": "test-business-secret"}
    transcript = _event(call_id, "transcript", role="user", transcriptType="final",
                        transcript="Je viens d'avoir un accident.", startMs=1500, endMs=3500)
    first = client.post("/v1/voice/vapi/server-events", headers=headers, json=transcript)
    assert first.status_code == 200, first.text
    assert first.json() == {"ignored": True}
    report = _event(call_id, "end-of-call-report", artifact={"messages": [
        {"role": "assistant", "message": "Bonjour", "startMs": 0, "endMs": 1000},
        {"role": "user", "message": "Je viens d'avoir un accident.", "startMs": 1500, "endMs": 3500},
    ], "recording": {"url": "https://attacker.invalid/ignore-this"}})
    saved = client.post("/v1/voice/vapi/server-events", headers=headers, json=report)
    assert saved.status_code == 200, saved.text
    case_id = saved.json()["session"]["case_id"]
    assert client.post("/v1/voice/vapi/server-events", headers=headers, json=report).json()["replayed"]
    assert client.post("/v1/voice/vapi/server-events", headers=headers,
                       json=_event(call_id, "call.artifact.upload")).json() == {"ignored": True}
    assert len(uploads) == 1 and uploads[0][1] == media
    owner = UUID("00000000-0000-4000-8000-000000000843")
    case = VoiceIntakeRepository(database_url).get_case(owner, UUID(case_id))
    assert case.voice_session.recording["status"] == "available"
    assert case.voice_session.recording["byte_size"] == len(media)
    assert case.voice_session.recording["sha256"] == hashlib.sha256(media).hexdigest()
    assert [(item["speaker"], item["start_ms"]) for item in case.voice_session.segments] == [
        ("assistant", 0), ("caller", 1500)]
    client.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(owner))
    read = client.get(f"/v1/voice/{case_id}/recording/read-url")
    assert read.status_code == 200 and read.json()["expires_in"] == 300
    client.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(uuid4()))
    assert client.get(f"/v1/voice/{case_id}/recording/read-url").status_code == 404
