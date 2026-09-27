from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Iterator, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit, parse_qs
from urllib.request import Request, urlopen


class StorageConfigurationError(ValueError):
    pass


class StorageUnavailableError(RuntimeError):
    pass


class StorageObjectNotFound(RuntimeError):
    pass


class StorageAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadSignature:
    token: str


@dataclass(frozen=True)
class StoredObjectInfo:
    byte_size: int
    mime_type: str


class StorageAdapter(Protocol):
    bucket: str

    def create_signed_upload(self, storage_path: str) -> UploadSignature: ...

    def inspect_object(self, storage_path: str) -> StoredObjectInfo: ...

    def create_signed_read_url(self, storage_path: str, expires_in: int) -> str: ...

    def upload_fixture(self, storage_path: str, data: bytes, mime_type: str) -> None: ...

    def read_object_chunks(self, storage_path: str) -> Iterator[bytes]: ...
    def read_object(self, storage_path: str, max_bytes: int) -> bytes: ...


class SupabaseStorageAdapter:
    """Server-only adapter for Supabase Storage REST operations.

    The service-role key is used only to issue signed URLs and inspect/upload
    the small, server-owned CCTV fixture. User file bytes go directly from the
    browser to Storage using the returned upload token.
    """

    def __init__(
        self,
        supabase_url: str,
        service_role_key: str,
        bucket: str = "claim-evidence",
        open_url=urlopen,
    ) -> None:
        base = supabase_url.strip().rstrip("/")
        if not base or not service_role_key.strip():
            raise StorageConfigurationError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for evidence storage."
            )
        parsed = urlsplit(base)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise StorageConfigurationError("Supabase Storage must use HTTPS outside local development.")
        self.base_url = base
        self.service_role_key = service_role_key.strip()
        self.bucket = bucket.strip()
        if not self.bucket:
            raise StorageConfigurationError("SUPABASE_STORAGE_BUCKET cannot be empty.")
        self._open_url = open_url

    @classmethod
    def from_env(cls, bucket: str | None = None) -> "SupabaseStorageAdapter":
        return cls(
            supabase_url=os.getenv("SUPABASE_URL", ""),
            service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            bucket=bucket or os.getenv("SUPABASE_STORAGE_BUCKET", "claim-evidence"),
        )

    def create_signed_upload(self, storage_path: str) -> UploadSignature:
        encoded = self._encoded_key(storage_path)
        response = self._json_request(
            "POST",
            f"/storage/v1/object/upload/sign/{quote(self.bucket, safe='')}/{encoded}",
            body={},
            extra_headers={"x-upsert": "false"},
        )
        upload_url = response.get("url") or response.get("signedURL") or response.get("signedUrl")
        if not isinstance(upload_url, str):
            raise StorageAdapterError("Storage did not return a signed upload URL.")
        token = parse_qs(urlsplit(urljoin(self.base_url, upload_url)).query).get("token", [None])[0]
        if not token:
            raise StorageAdapterError("Storage did not return an upload token.")
        return UploadSignature(token=token)

    def inspect_object(self, storage_path: str) -> StoredObjectInfo:
        encoded = self._encoded_key(storage_path)
        response = self._json_request(
            "GET",
            f"/storage/v1/object/info/{quote(self.bucket, safe='')}/{encoded}",
        )
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        size = response.get("size", metadata.get("size"))
        mime_type = response.get("contentType") or response.get("content_type") or metadata.get("mimetype")
        if not isinstance(size, int) or not isinstance(mime_type, str):
            raise StorageAdapterError("Storage returned incomplete object metadata.")
        return StoredObjectInfo(byte_size=size, mime_type=mime_type.split(";", 1)[0].strip().lower())

    def create_signed_read_url(self, storage_path: str, expires_in: int) -> str:
        encoded = self._encoded_key(storage_path)
        response = self._json_request(
            "POST",
            f"/storage/v1/object/sign/{quote(self.bucket, safe='')}/{encoded}",
            body={"expiresIn": expires_in},
        )
        signed_url = response.get("signedURL") or response.get("signedUrl")
        if not isinstance(signed_url, str):
            raise StorageAdapterError("Storage did not return a signed read URL.")
        parsed = urlsplit(signed_url)
        if parsed.scheme and parsed.netloc:
            return signed_url
        if signed_url.startswith("/storage/v1/"):
            return f"{self.base_url}{signed_url}"
        return f"{self.base_url}/storage/v1/{signed_url.lstrip('/')}"

    def upload_fixture(self, storage_path: str, data: bytes, mime_type: str) -> None:
        encoded = self._encoded_key(storage_path)
        self._request(
            "POST",
            f"/storage/v1/object/{quote(self.bucket, safe='')}/{encoded}",
            data=data,
            content_type=mime_type,
            extra_headers={"x-upsert": "true"},
            timeout=30,
        )

    def read_object_chunks(self, storage_path: str) -> Iterator[bytes]:
        """Read a private immutable object with server credentials, never a signed URL."""
        encoded = self._encoded_key(storage_path)
        request = Request(
            f"{self.base_url}/storage/v1/object/{quote(self.bucket, safe='')}/{encoded}",
            headers={"apikey": self.service_role_key, "Authorization": f"Bearer {self.service_role_key}"},
            method="GET",
        )
        try:
            with self._open_url(request, timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    yield chunk
        except HTTPError as error:
            if error.code == 404:
                raise StorageObjectNotFound("Storage object was not found.") from error
            raise StorageAdapterError(f"Storage read failed with status {error.code}.") from error
        except (URLError, TimeoutError, OSError) as error:
            raise StorageUnavailableError("Supabase Storage is unavailable.") from error
    def upload_private_object(self, storage_path: str, data: bytes, mime_type: str) -> None:
        """Write immutable server-owned media into the configured private bucket."""
        encoded = self._encoded_key(storage_path)
        self._request(
            "POST", f"/storage/v1/object/{quote(self.bucket, safe='')}/{encoded}",
            data=data, content_type=mime_type,
            extra_headers={"x-upsert": "false"}, timeout=30,
        )

    def read_object(self, storage_path: str, max_bytes: int) -> bytes:
        data = self._request(
            "GET", f"/storage/v1/object/authenticated/{quote(self.bucket, safe='')}/{self._encoded_key(storage_path)}",
            max_response_bytes=max_bytes + 1,
        )
        if len(data) > max_bytes:
            raise StorageAdapterError("Evidence exceeds the allowed download size.")
        return data

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        content_type: str | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: int = 8,
        max_response_bytes: int = 1_000_000,
    ) -> bytes:
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Accept": "application/json",
        }
        if content_type:
            headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with self._open_url(request, timeout=timeout) as response:
                return response.read(max_response_bytes)
        except HTTPError as error:
            if error.code == 404:
                raise StorageObjectNotFound("Storage object was not found.") from error
            raise StorageAdapterError(f"Storage request failed with status {error.code}.") from error
        except (URLError, TimeoutError, OSError) as error:
            raise StorageUnavailableError("Supabase Storage is unavailable.") from error

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        encoded_body = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"} if encoded_body is not None else {}
        if extra_headers:
            headers.update(extra_headers)
        content = self._request(method, path, data=encoded_body, extra_headers=headers)
        try:
            parsed = json.loads(content)
        except (TypeError, json.JSONDecodeError) as error:
            raise StorageAdapterError("Storage returned an invalid response.") from error
        if not isinstance(parsed, dict):
            raise StorageAdapterError("Storage returned an invalid response.")
        return parsed

    @staticmethod
    def _encoded_key(storage_path: str) -> str:
        if not storage_path or storage_path.startswith("/") or any(part in {".", ".."} for part in storage_path.split("/")):
            raise StorageAdapterError("Invalid Storage object path.")
        return quote(storage_path, safe="/")
