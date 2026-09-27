"""Insured-only deposit operations scoped by a sent SMS grant."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from claim_api.case_service import CaseReadOnlyError, StaleCaseError, merge_intake
from claim_api.deposit_grants import DepositGrantError
from claim_api.evidence_service import EvidenceService, EvidenceNotFoundError
from claim_api.demo_media import CATALOGUES, fixture
from claim_api.models import (
    CreateEvidenceUploadIntentRequest, EvidenceUploadIntentView, FinalizeEvidenceRequest,
    Intake, IntakePatch,
)
from claim_api.postgres_cases import DatabaseUnavailableError, PostgresCaseRepository, validate_database_url
from claim_api.storage import StorageAdapterError, StorageObjectNotFound, SupabaseStorageAdapter


SESSION_TTL = timedelta(minutes=15)
CORRECTABLE = frozenset({"insured_name", "insured_reference", "incident_at", "location", "narrative",
                         "danger_status", "injury_status", "vehicle_country"})
PUBLIC_INTAKE = ("insured_name", "insured_reference", "incident_at", "time_source", "location", "narrative",
                 "danger_status", "injury_status", "vehicle_country")
FOLLOW_UP_FIELDS = ("insured_name", "incident_at")


@dataclass(frozen=True)
class GuestPrincipal:
    session_id: UUID
    grant_id: UUID
    case_id: UUID
    case_owner_id: UUID
    expires_at: datetime
    capabilities: frozenset[str]


class InsuredPortalService:
    def __init__(self, database_url: str) -> None:
        self.database_url = validate_database_url(database_url)

    def _connect(self):
        try:
            return psycopg.connect(self.database_url, row_factory=dict_row,
                                   connect_timeout=5, prepare_threshold=None)
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            raise DatabaseUnavailableError("Database connection failed.") from exc

    @staticmethod
    def _digest(value: str) -> str:
        if not value or len(value) > 128 or not value.isascii():
            raise DepositGrantError("invalid_grant")
        return hashlib.sha256(value.encode("ascii")).hexdigest()

    @staticmethod
    def _check_grant(row: dict[str, Any] | None, *, session: bool = False) -> None:
        if row is None or row["message_id"] is None:
            raise DepositGrantError("invalid_session" if session else "invalid_grant")
        now = datetime.now(timezone.utc)
        if row["revoked_at"] is not None:
            raise DepositGrantError("revoked_grant")
        if row["grant_expires_at"] <= now:
            raise DepositGrantError("expired_grant")
        if session and row["session_expires_at"] <= now:
            raise DepositGrantError("expired_session")

    def exchange(self, link_token: str) -> dict[str, Any]:
        digest = self._digest(link_token)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""select g.*, g.expires_at as grant_expires_at,
                   c.state_version, c.content_revision
                from public.deposit_grants g join public.cases c on c.id=g.case_id
                where g.token_sha256=%s""", (digest,))
            grant = cursor.fetchone()
            self._check_grant(grant)
            raw_session = secrets.token_urlsafe(32)
            expires = min(datetime.now(timezone.utc) + SESSION_TTL, grant["grant_expires_at"])
            cursor.execute("""insert into public.deposit_sessions
                (grant_id, token_sha256, expires_at)
                values (%s,%s,%s) returning id""",
                (grant["id"], self._digest(raw_session), expires))
            session_id = cursor.fetchone()["id"]
            cursor.execute("""insert into public.audit_events
                (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                 content_revision_before, content_revision_after, metadata_json)
                values (%s,null,'deposit.session_started',%s,%s,%s,%s,%s)""",
                (grant["case_id"], grant["state_version"], grant["state_version"],
                 grant["content_revision"], grant["content_revision"],
                 Jsonb({"grant_id": str(grant["id"]), "session_id": str(session_id)})))
            return {"session_token": raw_session, "expires_at": expires}

    @staticmethod
    def _authorize_cursor(cursor, session_token: str, capability: str, *, lock_grant: bool = False) -> GuestPrincipal:
        if not session_token or len(session_token) > 128 or not session_token.isascii():
            raise DepositGrantError("invalid_session")
        digest = hashlib.sha256(session_token.encode("ascii")).hexdigest()
        cursor.execute("""select s.id as session_id, s.expires_at as session_expires_at,
            g.id as grant_id, g.case_id, g.message_id, g.capabilities,
            g.expires_at as grant_expires_at, g.revoked_at,
            c.created_by_user_id as case_owner_id
            from public.deposit_sessions s
            join public.deposit_grants g on g.id=s.grant_id
            join public.cases c on c.id=g.case_id
            where s.token_sha256=%s""" + (" for share of g" if lock_grant else ""), (digest,))
        row = cursor.fetchone()
        InsuredPortalService._check_grant(row, session=True)
        if capability not in row["capabilities"]:
            raise DepositGrantError("forbidden_capability")
        return GuestPrincipal(row["session_id"], row["grant_id"], row["case_id"],
                              row["case_owner_id"], min(row["session_expires_at"], row["grant_expires_at"]),
                              frozenset(row["capabilities"]))

    def authorize(self, session_token: str, capability: str) -> GuestPrincipal:
        with self._connect() as connection, connection.cursor() as cursor:
            return self._authorize_cursor(cursor, session_token, capability)

    @staticmethod
    def _summary(cursor, principal: GuestPrincipal) -> dict[str, Any]:
        cursor.execute("select state_version, content_revision, intake_json from public.cases where id=%s",
                       (principal.case_id,))
        case = cursor.fetchone()
        intake = Intake.model_validate(case["intake_json"]).model_dump(mode="json")
        public_intake = {name: intake.get(name) for name in PUBLIC_INTAKE}
        missing = [name for name in FOLLOW_UP_FIELDS if not (intake.get(name) or "").strip()]
        cursor.execute("""select id, kind, source_kind, mime_type, byte_size,
            checksum_status, original_filename, received_at from public.evidence
            where case_id=%s and source_kind in ('insured_upload','demo_fixture')
            order by received_at, id""", (principal.case_id,))
        evidence = [dict(row) for row in cursor.fetchall()]
        cursor.execute("""select recording_status, coalesce(jsonb_array_length(transcript_json),0) as turns
            from public.voice_sessions where case_id=%s""", (principal.case_id,))
        voice = cursor.fetchone()
        cursor.execute("""select exists(select 1 from public.deposit_grants g
            join public.case_messages m on m.id=g.message_id and m.case_id=g.case_id
            where g.id=%s and g.case_id=%s and m.channel='sms'
              and m.provider='twilio.sms.claimroom') as available""",
            (principal.grant_id, principal.case_id))
        conversation_available = cursor.fetchone()["available"]
        cursor.execute("""select status, assessment->'missing_information' as requests from public.case_accident_reviews
            where case_id=%s and content_revision=%s""", (principal.case_id,case['content_revision']))
        assessment = cursor.fetchone()
        return {"case_id": principal.case_id, "state_version": case["state_version"],
                "content_revision": case["content_revision"], "source_label": "Selon votre déclaration",
                "intake": public_intake, "missing_fields": missing,
                "transcript_available": bool(voice and voice["turns"]),
                "recording_status": voice["recording_status"] if voice else "unavailable",
                "evidence": evidence, "conversation_available": conversation_available,
                "analysis_status": assessment["status"] if assessment else None,
                "analysis_requests": (assessment["requests"] or []) if assessment else []}

    def summary(self, session_token: str) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            principal = self._authorize_cursor(cursor, session_token, "read_summary")
            return self._summary(cursor, principal)

    def chat_history(self, session_token: str) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            principal = self._authorize_cursor(cursor, session_token, "read_summary")
            cursor.execute("select revision, state_json from public.deposit_chat_history where case_id=%s",
                           (principal.case_id,))
            row = cursor.fetchone()
            return {"revision": row["revision"], "state": row["state_json"]} if row else {"revision": 0, "state": None}

    def save_chat_history(self, session_token: str, expected_revision: int,
                          state: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            principal = self._authorize_cursor(cursor, session_token, "correct_intake")
            # Same case -> grant lock order as corrections and revocation. The
            # case lock also serializes the first save from two different links.
            cursor.execute("select id from public.cases where id=%s for update", (principal.case_id,))
            self._authorize_cursor(cursor, session_token, "correct_intake", lock_grant=True)
            cursor.execute("select revision, state_json from public.deposit_chat_history where case_id=%s",
                           (principal.case_id,))
            row = cursor.fetchone()
            revision = row["revision"] if row else 0
            if row and row["state_json"] == state:
                return {"revision": revision, "state": state}  # Lost-response retry.
            if revision != expected_revision:
                raise DepositGrantError("stale_chat_history")
            if row:
                old = row["state_json"]
                messages = old["messages"]
                if (state["messages"][:len(messages)] != messages
                        or state["opening"] != old["opening"] or state["openingAt"] != old["openingAt"]):
                    raise DepositGrantError("stale_chat_history")
            cursor.execute("""insert into public.deposit_chat_history(case_id, revision, state_json)
                values (%s,%s,%s) on conflict (case_id) do update
                set revision=excluded.revision, state_json=excluded.state_json, updated_at=now()""",
                (principal.case_id, revision + 1, Jsonb(state)))
            return {"revision": revision + 1, "state": state}

    def correct(self, session_token: str, expected_state_version: int, field_name: str,
                value: Any, reason: str | None) -> dict[str, Any]:
        if field_name not in CORRECTABLE:
            raise DepositGrantError("forbidden_capability")
        with self._connect() as connection, connection.cursor() as cursor:
            principal = self._authorize_cursor(cursor, session_token, "correct_intake")
            cursor.execute("select * from public.cases where id=%s for update", (principal.case_id,))
            case = cursor.fetchone()
            # Serialize with revoke (which also locks case then grant) and
            # recheck after the case lock, before any mutation or early return.
            self._authorize_cursor(cursor, session_token, "correct_intake", lock_grant=True)
            if case["state_version"] != expected_state_version:
                raise StaleCaseError(case["state_version"])
            if case["status"] in {"registered", "sent"}:
                raise CaseReadOnlyError
            old = Intake.model_validate(case["intake_json"])
            patch = IntakePatch.model_validate({field_name: value})
            updated = merge_intake(old, patch)
            changed = updated.model_dump(mode="json")
            if field_name == "incident_at":
                changed["time_source"] = "insured_correction"
                updated = Intake.model_validate(changed)
            if old.model_dump(mode="json") == changed:
                return self._summary(cursor, principal)
            if field_name == "incident_at" and old.incident_at and updated.incident_at and old.incident_at.date() != updated.incident_at.date():
                cursor.execute("update public.case_counterparty_lookups set active=false, updated_at=now() where case_id=%s", (principal.case_id,))
            cursor.execute("""update public.cases set intake_json=%s,
                state_version=state_version+1, content_revision=content_revision+1,
                status='collecting', current_draft_id=null, updated_at=now()
                where id=%s returning *""", (Jsonb(changed), principal.case_id))
            new_case = cursor.fetchone()
            cursor.execute("update public.approvals set superseded_at=now() where case_id=%s and superseded_at is null", (principal.case_id,))
            old_json = old.model_dump(mode="json")
            cursor.execute("""insert into public.insured_corrections
                (case_id, grant_id, session_id, field_name, previous_value_json, new_value_json, reason, content_revision)
                values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (principal.case_id, principal.grant_id, principal.session_id, field_name,
                 Jsonb({"value": old_json[field_name]}), Jsonb({"value": changed[field_name]}),
                 reason, new_case["content_revision"]))
            cursor.execute("""insert into public.audit_events
                (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                 content_revision_before, content_revision_after, metadata_json)
                values (%s,null,'case.insured_correction',%s,%s,%s,%s,%s)""",
                (principal.case_id, case["state_version"], new_case["state_version"],
                 case["content_revision"], new_case["content_revision"],
                 Jsonb({"field": field_name, "grant_id": str(principal.grant_id)})))
            return self._summary(cursor, principal)

    def _evidence_service(self) -> EvidenceService:
        from claim_api.media_workflow import MediaWorkflow, MediaWorkflowRepository
        repository = PostgresCaseRepository(self.database_url)
        storage = SupabaseStorageAdapter.from_env()
        return EvidenceService(repository, storage,
            on_media_received=MediaWorkflow(MediaWorkflowRepository(self.database_url)).received)

    def create_upload_intent(self, session_token: str, request: CreateEvidenceUploadIntentRequest) -> EvidenceUploadIntentView:
        principal = self.authorize(session_token, "upload_evidence")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select status from public.cases where id=%s", (principal.case_id,))
            if cursor.fetchone()["status"] in {"registered", "sent"}:
                raise CaseReadOnlyError
        return self._evidence_service().create_upload_intent(
            str(principal.case_owner_id), principal.case_id, request,
            source_kind="insured_upload", deposit_grant_id=principal.grant_id)

    def finalize_upload(self, session_token: str, request: FinalizeEvidenceRequest) -> dict[str, Any]:
        principal = self.authorize(session_token, "upload_evidence")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""select id from public.evidence_upload_intents
                where case_id=%s and storage_path=%s and deposit_grant_id=%s
                  and source_kind='insured_upload'""",
                (principal.case_id, request.storage_path, principal.grant_id))
            if cursor.fetchone() is None:
                raise EvidenceNotFoundError
        self._evidence_service().finalize_upload(str(principal.case_owner_id), principal.case_id, request,
                                                 verify_bytes=True)
        return self.summary(session_token)

    def signed_read_url(self, session_token: str, evidence_id: UUID) -> dict[str, Any]:
        principal = self.authorize(session_token, "read_summary")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""select storage_path from public.evidence where id=%s and case_id=%s
                and source_kind in ('insured_upload','demo_fixture')""",
                (evidence_id, principal.case_id))
            row = cursor.fetchone()
            if row is None:
                raise EvidenceNotFoundError
        result = self._evidence_service().signed_read_url(str(principal.case_owner_id), principal.case_id, evidence_id)
        with self._connect() as connection, connection.cursor() as cursor:
            self._authorize_cursor(cursor, session_token, "read_summary")
            cursor.execute("select state_version, content_revision from public.cases where id=%s", (principal.case_id,))
            case = cursor.fetchone()
            cursor.execute("""insert into public.audit_events
                (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                 content_revision_before, content_revision_after, metadata_json)
                values (%s,null,'deposit.evidence_read_url_issued',%s,%s,%s,%s,%s)""",
                (principal.case_id, case["state_version"], case["state_version"],
                 case["content_revision"], case["content_revision"],
                 Jsonb({"evidence_id": str(evidence_id), "grant_id": str(principal.grant_id)})))
        return result.model_dump(mode="json")

    def demo_media(self, session_token: str) -> list[dict[str, Any]]:
        principal = self.authorize(session_token, "attach_demo_media")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select scenario_id from public.cases where id=%s", (principal.case_id,))
            scenario_id = cursor.fetchone()["scenario_id"]
            catalogue = CATALOGUES.get(scenario_id)
            if catalogue is None:
                return []
            cursor.execute("select media_key from public.deposit_demo_media where case_id=%s", (principal.case_id,))
            attached = {row["media_key"] for row in cursor.fetchall()}
        return [{"id": item.filename, "kind": item.kind, "mime_type": item.mime_type,
                 "role": item.role, "provenance": "demo_fixture", "version": catalogue.version,
                 "duration_seconds": item.duration_seconds, "thumbnail_key": item.thumbnail_key,
                 "subject": item.subject, "provenance_note": item.provenance_note,
                 "attached": item.filename in attached} for item in catalogue.items]

    def demo_preview(self, session_token: str, media_key: str) -> tuple[bytes, str]:
        principal = self.authorize(session_token, "attach_demo_media")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select scenario_id from public.cases where id=%s", (principal.case_id,))
            scenario_id = cursor.fetchone()["scenario_id"]
        selected = fixture(scenario_id, media_key)
        if selected is None:
            raise EvidenceNotFoundError
        _catalogue, item, path = selected
        return path.read_bytes(), item.mime_type

    def attach_demo(self, session_token: str, media_key: str, expected_state_version: int) -> dict[str, Any]:
        principal = self.authorize(session_token, "attach_demo_media")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select scenario_id, status from public.cases where id=%s", (principal.case_id,))
            first_case = cursor.fetchone()
            if first_case["status"] in {"registered", "sent"}:
                raise CaseReadOnlyError
            selected = fixture(first_case["scenario_id"], media_key)
            if selected is None:
                raise EvidenceNotFoundError
            cursor.execute("select evidence_id from public.deposit_demo_media where case_id=%s and media_key=%s",
                           (principal.case_id, media_key))
            if cursor.fetchone() is not None:
                return self.summary(session_token)
        catalogue, item, path = selected
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        storage_path = f"{principal.case_id}/deposit-demo/{catalogue.version}/{media_key}"
        storage = SupabaseStorageAdapter.from_env()
        upload_error: StorageAdapterError | None = None
        try:
            storage.upload_fixture(storage_path, data, item.mime_type)
        except StorageAdapterError as error:
            # A retry may follow a successful Storage write that preceded a
            # failed database transaction. Reuse only the exact same bytes.
            upload_error = error
        stored_digest = hashlib.sha256()
        try:
            for chunk in storage.read_object_chunks(storage_path):
                stored_digest.update(chunk)
        except StorageObjectNotFound:
            if upload_error is not None:
                raise upload_error
            raise DepositGrantError("invalid_upload") from None
        if stored_digest.hexdigest() != digest:
            raise DepositGrantError("invalid_upload")
        stored = storage.inspect_object(storage_path)
        if stored.byte_size != len(data) or stored.mime_type != item.mime_type:
            raise DepositGrantError("invalid_upload")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select * from public.cases where id=%s for update", (principal.case_id,))
            case = cursor.fetchone()
            self._authorize_cursor(cursor, session_token, "attach_demo_media", lock_grant=True)
            if case["status"] in {"registered", "sent"}:
                raise CaseReadOnlyError
            cursor.execute("select evidence_id from public.deposit_demo_media where case_id=%s and media_key=%s",
                           (principal.case_id, media_key))
            if cursor.fetchone() is not None:
                return self._summary(cursor, principal)
            if case["state_version"] != expected_state_version:
                raise StaleCaseError(case["state_version"])
            evidence_id = uuid4()
            cursor.execute("""insert into public.evidence
                (id, case_id, storage_path, kind, source_kind, mode, mime_type, byte_size,
                 client_sha256, checksum_status, sha256_verified, role)
                values (%s,%s,%s,%s,'demo_fixture','mock',%s,%s,%s,'verified',%s,%s)""",
                (evidence_id, principal.case_id, storage_path, item.kind, item.mime_type,
                 len(data), digest, digest, item.role))
            cursor.execute("""insert into public.deposit_demo_media
                (case_id, media_key, evidence_id, attached_by_grant_id)
                values (%s,%s,%s,%s)""", (principal.case_id, media_key, evidence_id, principal.grant_id))
            cursor.execute("""update public.cases set state_version=state_version+1,
                content_revision=content_revision+1, status='collecting', current_draft_id=null,
                updated_at=now() where id=%s returning *""", (principal.case_id,))
            updated = cursor.fetchone()
            cursor.execute("update public.approvals set superseded_at=now() where case_id=%s and superseded_at is null", (principal.case_id,))
            cursor.execute("""insert into public.audit_events
                (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                 content_revision_before, content_revision_after, metadata_json)
                values (%s,null,'case.demo_media_attached',%s,%s,%s,%s,%s)""",
                (principal.case_id, case["state_version"], updated["state_version"],
                 case["content_revision"], updated["content_revision"],
                 Jsonb({"media_key": media_key, "evidence_id": str(evidence_id),
                        "grant_id": str(principal.grant_id), "fixture_version": catalogue.version})))
            from claim_api.media_workflow import enqueue_media
            enqueue_media(cursor, principal.case_id, updated["content_revision"])
        # Commit the evidence and durable job before calling providers.
        from claim_api.media_workflow import MediaWorkflow, MediaWorkflowRepository
        MediaWorkflow(MediaWorkflowRepository(self.database_url)).tick(principal.case_id)
        return self.summary(session_token)
