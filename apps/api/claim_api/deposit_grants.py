"""Case-scoped insured deposit links, independent from manager authentication.

Only the issuance result contains the raw bearer token. Do not log or serialize
that URL outside the authenticated preview and the private SMS body.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from claim_api.postgres_cases import DatabaseUnavailableError, validate_database_url


CAPABILITIES = frozenset({
    "read_summary", "correct_intake", "upload_evidence", "attach_demo_media",
})
DEFAULT_CAPABILITIES = (
    "read_summary", "correct_intake", "upload_evidence", "attach_demo_media",
)
DEFAULT_TTL = timedelta(hours=24)
MAX_TTL = timedelta(days=7)


class DepositGrantError(Exception):
    """Public code without any raw link, token or case content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class IssuedDepositGrant:
    grant_id: UUID
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class ValidatedDepositGrant:
    grant_id: UUID
    case_id: UUID
    content_revision: int
    capabilities: frozenset[str]
    expires_at: datetime
    message_id: UUID | None


class DepositGrantService:
    def __init__(self, database_url: str, portal_base_url: str | None = None) -> None:
        self.database_url = validate_database_url(database_url)
        base = (portal_base_url or os.getenv("DEPOSIT_PORTAL_BASE_URL", "")).strip().rstrip("/")
        parsed = urlsplit(base)
        local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        if (parsed.scheme != "https" and not local_http) or not parsed.netloc or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("DEPOSIT_PORTAL_BASE_URL must be an HTTPS page URL (localhost HTTP is allowed).")
        self.portal_base_url = base

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        try:
            return psycopg.connect(
                self.database_url, row_factory=dict_row, connect_timeout=5,
                prepare_threshold=None,
            )
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            raise DatabaseUnavailableError("Database connection failed.") from exc

    @staticmethod
    def _audit(cursor: psycopg.Cursor[dict[str, Any]], case: dict[str, Any],
               actor_id: UUID | None, event_type: str, grant_id: UUID) -> None:
        cursor.execute("""insert into public.audit_events
            (case_id, actor_user_id, event_type, state_version_before, state_version_after,
             content_revision_before, content_revision_after, metadata_json)
            values (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (case["id"], actor_id, event_type, case["state_version"], case["state_version"],
             case["content_revision"], case["content_revision"], Jsonb({"grant_id": str(grant_id)})))

    def issue(self, case_id: UUID, content_revision: int,
              ttl: timedelta = DEFAULT_TTL, *, actor_id: UUID | None = None,
              capabilities: tuple[str, ...] = DEFAULT_CAPABILITIES,
              expected_state_version: int | None = None) -> IssuedDepositGrant:
        if ttl <= timedelta(0) or ttl > MAX_TTL:
            raise DepositGrantError("invalid_ttl")
        if not capabilities or len(set(capabilities)) != len(capabilities) or not set(capabilities) <= CAPABILITIES:
            raise DepositGrantError("forbidden_capability")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = datetime.now(timezone.utc)
        expires_at = now + ttl
        with self._connect() as connection, connection.cursor() as cursor:
            query = "select id, created_by_user_id, content_revision, state_version from public.cases where id = %s"
            values: tuple[Any, ...] = (case_id,)
            if actor_id is not None:
                query += " and created_by_user_id = %s"
                values += (actor_id,)
            query += " for update"
            cursor.execute(query, values)
            case = cursor.fetchone()
            if case is None:
                raise DepositGrantError("not_found")
            if case["content_revision"] != content_revision:
                raise DepositGrantError("stale_case")
            if expected_state_version is not None and case["state_version"] != expected_state_version:
                raise DepositGrantError("stale_case")
            cursor.execute("""insert into public.deposit_grants
                (case_id, token_sha256, capabilities, content_revision, expires_at)
                values (%s, %s, %s, %s, %s) returning id""",
                (case_id, digest, list(capabilities), content_revision, expires_at))
            grant_id = cursor.fetchone()["id"]
            self._audit(cursor, case, actor_id, "deposit_grant.issued", grant_id)
        return IssuedDepositGrant(grant_id=grant_id,
                                  url=f"{self.portal_base_url}#token={token}",
                                  expires_at=expires_at)

    @staticmethod
    def _checked(row: dict[str, Any] | None, token: str, case_id: UUID,
                 content_revision: int | None, required_capability: str,
                 *, require_unassociated: bool) -> ValidatedDepositGrant:
        if required_capability not in CAPABILITIES:
            raise DepositGrantError("forbidden_capability")
        if row is None or not token or len(token) > 128 or not token.isascii():
            raise DepositGrantError("invalid_grant")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        if not hmac.compare_digest(digest, row["token_sha256"]) or row["case_id"] != case_id:
            raise DepositGrantError("invalid_grant")
        if row["revoked_at"] is not None:
            raise DepositGrantError("revoked_grant")
        if row["expires_at"] <= datetime.now(timezone.utc):
            raise DepositGrantError("expired_grant")
        if content_revision is not None and row["content_revision"] != content_revision:
            raise DepositGrantError("stale_case")
        # The preview must still describe the exact version sent in the SMS.
        # After association, insured corrections legitimately advance the case
        # revision while the same grant remains usable for later uploads.
        if require_unassociated and row["current_revision"] != row["content_revision"]:
            raise DepositGrantError("stale_case")
        if required_capability not in row["capabilities"]:
            raise DepositGrantError("forbidden_capability")
        if require_unassociated and row["message_id"] is not None:
            raise DepositGrantError("grant_already_associated")
        if not require_unassociated and row["message_id"] is None:
            raise DepositGrantError("invalid_grant")
        return ValidatedDepositGrant(
            grant_id=row["id"], case_id=row["case_id"],
            content_revision=row["content_revision"],
            capabilities=frozenset(row["capabilities"]),
            expires_at=row["expires_at"], message_id=row["message_id"],
        )

    @staticmethod
    def _select(cursor: psycopg.Cursor[dict[str, Any]], grant_id: UUID, *, lock: bool) -> dict[str, Any] | None:
        cursor.execute("""select g.*, c.content_revision as current_revision
            from public.deposit_grants g join public.cases c on c.id = g.case_id
            where g.id = %s""" + (" for update of g, c" if lock else ""), (grant_id,))
        return cursor.fetchone()

    def validate_url(self, cursor: psycopg.Cursor[dict[str, Any]], grant_id: UUID,
                     case_id: UUID, url: str, content_revision: int,
                     required_capability: str = "read_summary") -> ValidatedDepositGrant:
        """Validate a preview URL while holding its row lock in the SMS transaction."""
        if not isinstance(url, str) or not url.startswith(f"{self.portal_base_url}#token="):
            raise DepositGrantError("invalid_grant")
        token = url[len(self.portal_base_url) + len("#token="):]
        if url != f"{self.portal_base_url}#token={token}" or any(ch in token for ch in "#?&=%"):
            raise DepositGrantError("invalid_grant")
        return self._checked(self._select(cursor, grant_id, lock=True), token, case_id,
                             content_revision, required_capability, require_unassociated=True)

    def validate(self, token: str, case_id: UUID, required_capability: str = "read_summary") -> ValidatedDepositGrant:
        """Validate an already associated link before each guest operation."""
        if not isinstance(token, str) or not token or len(token) > 128 or not token.isascii():
            raise DepositGrantError("invalid_grant")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""select g.*, c.content_revision as current_revision
                from public.deposit_grants g join public.cases c on c.id = g.case_id
                where g.token_sha256 = %s""", (digest,))
            return self._checked(cursor.fetchone(), token, case_id, None,
                                 required_capability, require_unassociated=False)

    def associate(self, cursor: psycopg.Cursor[dict[str, Any]], grant_id: UUID,
                  message_id: UUID, case_id: UUID) -> None:
        """Call after inserting the SMS, in the same transaction as validate_url."""
        cursor.execute("""update public.deposit_grants g set message_id = %s
            where g.id = %s and g.case_id = %s and g.message_id is null
              and g.revoked_at is null and g.expires_at > now()
              and exists (select 1 from public.cases c where c.id = g.case_id
                          and c.content_revision = g.content_revision)
              and exists (select 1 from public.case_messages m
                          where m.id = %s and m.case_id = g.case_id)
            returning g.id""", (message_id, grant_id, case_id, message_id))
        if cursor.fetchone() is None:
            raise DepositGrantError("invalid_grant")

    def revoke(self, grant_id: UUID, case_id: UUID, *, actor_id: UUID | None = None,
               expected_state_version: int | None = None) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            query = "select id, created_by_user_id, content_revision, state_version from public.cases where id = %s"
            values: tuple[Any, ...] = (case_id,)
            if actor_id is not None:
                query += " and created_by_user_id = %s"
                values += (actor_id,)
            query += " for update"
            cursor.execute(query, values)
            case = cursor.fetchone()
            if case is None:
                raise DepositGrantError("not_found")
            if expected_state_version is not None and case["state_version"] != expected_state_version:
                raise DepositGrantError("stale_case")
            cursor.execute("""update public.deposit_grants
                set revoked_at = now(), grant_version = grant_version + 1
                where id = %s and case_id = %s and revoked_at is null
                returning id""", (grant_id, case_id))
            if cursor.fetchone() is None:
                raise DepositGrantError("not_found")
            self._audit(cursor, case, actor_id, "deposit_grant.revoked", grant_id)
