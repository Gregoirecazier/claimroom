"""Fetch Vapi's original stereo artifact and store its bytes unchanged."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from claim_api.storage import StorageAdapterError, SupabaseStorageAdapter
from claim_api.voice_repository import VoiceIntakeRepository


# Supabase's configured project/bucket limit is 50 MB, not 50 MiB.
MAX_RECORDING_BYTES = 50_000_000


class RecordingError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class OriginalRecording:
    data: bytes
    mime_type: str
    extension: str
    sha256: str


class VapiRecordingFetcher:
    def __init__(self, private_key: str, allowed_hosts: set[str], open_url=None):
        self.private_key = private_key
        self.allowed_hosts = allowed_hosts
        self.open_url = open_url or build_opener(_NoRedirect()).open

    @classmethod
    def from_env(cls) -> "VapiRecordingFetcher":
        key = os.getenv("VAPI_PRIVATE_API_KEY", "")
        hosts = {host.strip().lower() for host in os.getenv("VAPI_RECORDING_ALLOWED_HOSTS", "").split(",") if host.strip()}
        if not key or not hosts:
            raise RecordingError("recording_not_configured")
        return cls(key, hosts)

    def fetch(self, call_id: str) -> OriginalRecording:
        if not call_id or len(call_id) > 200 or not all(c.isalnum() or c in "-_" for c in call_id):
            raise RecordingError("invalid_call_id")
        request = Request(f"https://api.vapi.ai/call/{quote(call_id, safe='')}/stereo-recording",
                          headers={"Authorization": f"Bearer {self.private_key}",
                                   "User-Agent": "Claimroom/1.0"})
        try:
            response = self.open_url(request, timeout=20)
        except HTTPError as error:
            if error.code != 302:
                raise RecordingError("vapi_recording_unavailable") from error
            signed_url = error.headers.get("Location")
        else:
            # Vapi documents a 302; accepting a direct body would risk storing
            # an error JSON payload as a recording.
            response.close()
            raise RecordingError("unexpected_vapi_recording_response")
        parsed = urlsplit(signed_url or "")
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts or parsed.username or parsed.password:
            raise RecordingError("invalid_recording_redirect")
        try:
            with self.open_url(Request(signed_url), timeout=60) as media:
                data = media.read(MAX_RECORDING_BYTES + 1)
        except Exception as error:
            raise RecordingError("recording_download_failed") from error
        if not data or len(data) > MAX_RECORDING_BYTES:
            raise RecordingError("invalid_recording_size")
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            if len(data) < 24 or int.from_bytes(data[22:24], "little") != 2:
                raise RecordingError("recording_not_stereo")
            mime_type, extension = "audio/wav", "wav"
        elif data[:3] == b"ID3" or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
            mime_type, extension = "audio/mpeg", "mp3"
        else:
            raise RecordingError("unsupported_recording_format")
        return OriginalRecording(data, mime_type, extension, hashlib.sha256(data).hexdigest())


def capture_original_recording(repository: VoiceIntakeRepository, actor_id: UUID,
                               call_id: str, storage=None, fetcher=None) -> None:
    state = repository.get_state(actor_id, call_id)
    if state is None or state["recording_status"] == "available":
        return
    try:
        source = fetcher or VapiRecordingFetcher.from_env()
        destination = storage or SupabaseStorageAdapter.from_env(
            bucket=os.getenv("VOICE_RECORDING_BUCKET", "claim-voice-recordings"))
        recording = source.fetch(call_id)
        path = f"{state['case_id']}/voice/stereo-original-{recording.sha256}.{recording.extension}"
        try:
            destination.upload_private_object(path, recording.data, recording.mime_type)
        except StorageAdapterError:
            # A previous attempt may have stored the object before its database
            # update failed. Accept that retry only after checking the bytes.
            existing = destination.inspect_object(path)
            if existing.byte_size != len(recording.data) or existing.mime_type != recording.mime_type:
                raise
            digest = hashlib.sha256()
            size = 0
            for chunk in destination.read_object_chunks(path):
                size += len(chunk)
                if size > MAX_RECORDING_BYTES:
                    raise RecordingError("recording_conflict")
                digest.update(chunk)
            if size != len(recording.data) or digest.hexdigest() != recording.sha256:
                raise RecordingError("recording_conflict")
        repository.update_recording(actor_id, call_id, status="available", storage_path=path,
                                    mime_type=recording.mime_type, byte_size=len(recording.data),
                                    sha256=recording.sha256)
    except RecordingError as error:
        repository.update_recording(actor_id, call_id, status="error", error_code=error.code)
    except Exception:
        # Secrets, short-lived source URLs, and media bytes never enter audit or logs.
        repository.update_recording(actor_id, call_id, status="error", error_code="recording_storage_or_provider_failed")
