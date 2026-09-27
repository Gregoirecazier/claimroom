"""Case-scoped S09 preview, immutable mock message, and private history."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from claim_api.deposit_grants import DepositGrantError
from claim_api.models import Intake
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.sms_content import compose_follow_up
from claim_api.sms_models import (
    RecipientConfirmation, SmsCreateRequest, SmsCreateResponse, SmsLinkResponse,
    SmsMessageView, SmsMockTransitionRequest, SmsMockTransitionResponse,
    SmsPreview, SmsPreviewRequest, WhatsAppInboundView,
)
from claim_api.sms_recipient import RecipientLookupUnavailable, VapiCallerLookup, mask_e164, validate_e164


PROVIDER = "claimroom.mock.whatsapp"


class DepositGrants(Protocol):
    def issue(self, case_id: UUID, content_revision: int, ttl=...): ...
    def validate_url(self, cursor, grant_id: UUID, case_id: UUID, url: str,
                     content_revision: int, required_capability: str = "read_summary"): ...
    def associate(self, cursor, grant_id: UUID, message_id: UUID, case_id: UUID) -> None: ...


class SmsError(ValueError):
    def __init__(self, code: str, status_code: int, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


@dataclass(frozen=True)
class CaseSnapshot:
    case_id: UUID
    scenario_id: str
    state_version: int
    content_revision: int
    intake: Intake
    evidence_kinds: frozenset[str]
    voice_status: str | None
    voice_facts: tuple[dict, ...]
    call_id: str | None


def payload_hash(*, recipient: str, body: str, grant_id: UUID, content_revision: int,
                 provider: str = PROVIDER) -> str:
    canonical = json.dumps({"channel": "whatsapp", "mode": "mock", "provider": provider,
                            "recipient": recipient, "body": body, "grant_id": str(grant_id),
                            "content_revision": content_revision},
                           sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _masked_body(body: str) -> str:
    return "\n".join("Accès sécurisé à votre dossier : [lien masqué]"
                     if line.startswith("Accès sécurisé à votre dossier : ") else line
                     for line in body.splitlines())


def _deposit_url(body: str) -> str | None:
    prefix = "Accès sécurisé à votre dossier : "
    line = next((line for line in body.splitlines() if line.startswith(prefix)), None)
    return line[len(prefix):] if line else None


def _message_view(row: dict) -> SmsMessageView:
    return SmsMessageView(
        id=row["id"], case_id=row["case_id"], recipient_masked=mask_e164(row["recipient"]),
        body_preview=_masked_body(row["body"]), mode=row["mode"], provider=row["provider"],
        status=row["status"], error_code=row["error_code"], created_at=row["created_at"],
        updated_at=row["updated_at"], sent_at=row["sent_at"], delivered_at=row["delivered_at"],
    )


class CaseMessageService(PostgresCaseRepository):
    def __init__(self, database_url: str, grants: DepositGrants,
                 caller_lookup: VapiCallerLookup | None = None, gateway: object | None = None):
        super().__init__(database_url)
        self.grants = grants
        self.caller_lookup = caller_lookup
        self.gateway = gateway
        self.provider = getattr(gateway, "provider", PROVIDER)

    @staticmethod
    def _snapshot(cursor, actor_id: UUID, case_id: UUID, *, lock: bool = False) -> CaseSnapshot:
        cursor.execute(
            "select * from public.cases where id = %s and created_by_user_id = %s" +
            (" for update" if lock else ""), (case_id, actor_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise SmsError("not_found", 404, "Case not found")
        cursor.execute("select kind from public.evidence where case_id = %s", (case_id,))
        evidence_kinds = frozenset(item["kind"] for item in cursor.fetchall())
        cursor.execute("""select provider_call_id, triage_json, facts_json from public.voice_sessions
                          where case_id = %s order by call_started_at desc limit 1""", (case_id,))
        voice = cursor.fetchone()
        return CaseSnapshot(
            case_id=row["id"], scenario_id=row["scenario_id"], state_version=row["state_version"],
            content_revision=row["content_revision"], intake=Intake.model_validate(row["intake_json"]),
            evidence_kinds=evidence_kinds,
            voice_status=(voice["triage_json"] or {}).get("status") if voice else None,
            voice_facts=tuple(voice["facts_json"] or ()) if voice else (),
            call_id=voice["provider_call_id"] if voice else None,
        )

    @staticmethod
    def _check_ready(snapshot: CaseSnapshot) -> list[str]:
        if snapshot.voice_status == "urgent_human_handoff":
            raise SmsError("urgent_handoff", 409, "Urgent voice intake needs a human")
        if snapshot.voice_status in {"collecting", "error"}:
            raise SmsError("voice_intake_incomplete", 409, "Voice intake cannot be followed up yet")
        if snapshot.voice_status == "incomplete":
            return ["voice_intake_incomplete"]
        return ["no_voice_session"] if snapshot.voice_status is None else []

    def _recipient(self, snapshot: CaseSnapshot, confirmation: RecipientConfirmation) -> str:
        if confirmation.kind == "manager_correction":
            try:
                return validate_e164(confirmation.number or "")
            except ValueError as error:
                raise SmsError("invalid_recipient", 422, str(error)) from error
        if snapshot.call_id is None:
            raise SmsError("caller_number_unavailable", 409, "This case has no inbound call")
        if self.caller_lookup is None:
            raise SmsError("caller_lookup_unconfigured", 503, "Caller lookup is unavailable")
        try:
            candidate = self.caller_lookup.candidate(snapshot.call_id)
        except RecipientLookupUnavailable as error:
            raise SmsError("caller_lookup_unavailable", 503, "Caller lookup failed") from error
        if candidate is None:
            raise SmsError("caller_number_unavailable", 409, "The inbound call has no E.164 caller number")
        return candidate

    def caller_candidate(self, actor_id: UUID, case_id: UUID) -> str:
        with self._connection() as connection, connection.cursor() as cursor:
            snapshot = self._snapshot(cursor, actor_id, case_id)
        return mask_e164(self._recipient(snapshot, RecipientConfirmation(kind="caller_id")))

    def auto_fake_follow_up(self, actor_id: UUID, case_id: UUID) -> SmsMessageView:
        """Create one fake follow-up for a completed live phone call, without a manager preview."""
        if self.provider != "claimroom.fake_whatsapp":
            raise SmsError("fake_whatsapp_not_configured", 404, "Fake WhatsApp is unavailable")
        # Hold the same lock across preview, creation, and dispatch. Each step
        # uses its own transaction, so two open manager tabs cannot issue two
        # messages for the same call.
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           (f"fake-whatsapp:{case_id}",))
            snapshot = self._snapshot(cursor, actor_id, case_id)
            cursor.execute("""select mode, telephony_provider, triage_json
                              from public.voice_sessions where case_id = %s
                              order by call_started_at desc limit 1""", (case_id,))
            voice = cursor.fetchone()
            if (voice is None or voice["mode"] != "live" or voice["telephony_provider"] != "twilio"
                    or (voice["triage_json"] or {}).get("status") not in {"complete", "incomplete"}):
                raise SmsError("fake_follow_up_unavailable", 409, "The phone call is not ready for follow-up")
            cursor.execute("""select * from public.case_messages
                              where case_id = %s and channel = 'whatsapp' and provider = %s
                              order by created_at desc, id desc limit 1""", (case_id, self.provider))
            existing = cursor.fetchone()
            if existing is not None:
                return (self.dispatch_fake(actor_id, case_id, existing["id"])
                        if existing["status"] == "queued" else _message_view(existing))
            recipient_confirmation = RecipientConfirmation(kind="caller_id")
            preview = self.preview(actor_id, case_id, SmsPreviewRequest(
                expected_state_version=snapshot.state_version,
                recipient_confirmation=recipient_confirmation,
            ))
            result = self.create(actor_id, case_id, SmsCreateRequest(
                expected_state_version=snapshot.state_version,
                idempotency_key=f"auto-fake:{case_id}:{uuid4()}",
                recipient_confirmation=recipient_confirmation,
                deposit_grant_id=preview.deposit_grant_id, deposit_url=preview.deposit_url,
                preview_hash=preview.preview_hash,
            ))
            return (self.dispatch_fake(actor_id, case_id, result.message.id)
                    if result.message.status == "queued" else result.message)

    def preview(self, actor_id: UUID, case_id: UUID, request: SmsPreviewRequest) -> SmsPreview:
        with self._connection() as connection, connection.cursor() as cursor:
            snapshot = self._snapshot(cursor, actor_id, case_id)
        if snapshot.state_version != request.expected_state_version:
            raise SmsError("stale_case", 409, "Case changed before preview",
                           {"current_state_version": snapshot.state_version})
        warnings = self._check_ready(snapshot)
        recipient = self._recipient(snapshot, request.recipient_confirmation)
        grant = self.grants.issue(case_id, snapshot.content_revision, actor_id=actor_id,
                                  expected_state_version=request.expected_state_version)
        content = compose_follow_up(
            snapshot.intake, evidence_kinds=set(snapshot.evidence_kinds), facts=list(snapshot.voice_facts),
            deposit_url=grant.url, synthetic_demo=snapshot.scenario_id == "g1" and snapshot.call_id is None,
        )
        digest = payload_hash(recipient=recipient, body=content.body,
                              grant_id=grant.grant_id, content_revision=snapshot.content_revision,
                              provider=self.provider)
        return SmsPreview(
            recipient_masked=mask_e164(recipient), body=content.body,
            deposit_url=grant.url,
            missing_items=list(content.missing_items), source_refs=list(content.source_refs),
            deposit_grant_id=grant.grant_id, deposit_link_expires_at=grant.expires_at,
            content_revision=snapshot.content_revision, preview_hash=digest,
            warnings=warnings + list(content.warnings),
        )

    def create(self, actor_id: UUID, case_id: UUID, request: SmsCreateRequest) -> SmsCreateResponse:
        # Resolve Vapi outside the row lock; verify the call and revision again inside.
        with self._connection() as connection, connection.cursor() as cursor:
            prior = self._snapshot(cursor, actor_id, case_id)
        recipient = self._recipient(prior, request.recipient_confirmation)
        with self._connection() as connection, connection.cursor() as cursor:
            snapshot = self._snapshot(cursor, actor_id, case_id, lock=True)
            cursor.execute("""select * from public.case_messages
                              where case_id = %s and channel = 'whatsapp' and idempotency_key = %s""",
                           (case_id, request.idempotency_key))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("""select id from public.deposit_grants
                                  where id = %s and case_id = %s and message_id = %s""",
                               (request.deposit_grant_id, case_id, existing["id"]))
                same_grant = cursor.fetchone() is not None
                if (existing["payload_hash"] != request.preview_hash or
                        existing["recipient"] != recipient or not same_grant or
                        existing["body"].splitlines()[-1] !=
                        "Accès sécurisé à votre dossier : " + request.deposit_url):
                    raise SmsError("idempotency_conflict", 409, "Idempotency key has another payload")
                return SmsCreateResponse(message=_message_view(existing), replayed=True,
                                         state_version=snapshot.state_version)
            self._check_ready(snapshot)
            if snapshot.call_id != prior.call_id:
                raise SmsError("stale_case", 409, "Voice session changed")
            if snapshot.state_version != request.expected_state_version:
                raise SmsError("stale_case", 409, "Case changed before message creation",
                               {"current_state_version": snapshot.state_version})
            content = compose_follow_up(
                snapshot.intake, evidence_kinds=set(snapshot.evidence_kinds), facts=list(snapshot.voice_facts),
                deposit_url=request.deposit_url,
                synthetic_demo=snapshot.scenario_id == "g1" and snapshot.call_id is None,
            )
            digest = payload_hash(recipient=recipient, body=content.body,
                                  grant_id=request.deposit_grant_id,
                                  content_revision=snapshot.content_revision, provider=self.provider)
            if digest != request.preview_hash:
                raise SmsError("stale_preview", 409, "WhatsApp preview changed")
            try:
                self.grants.validate_url(cursor, request.deposit_grant_id, case_id, request.deposit_url,
                                         snapshot.content_revision)
            except DepositGrantError as error:
                raise SmsError(error.code, 409, "Deposit link is invalid or expired") from error
            cursor.execute("""insert into public.case_messages
                          (case_id, channel, mode, recipient, body, status, provider,
                           idempotency_key, payload_hash)
                          values (%s, 'whatsapp', 'mock', %s, %s, 'queued', %s, %s, %s) returning *""",
                           (case_id, recipient, content.body, self.provider, request.idempotency_key, digest))
            message = cursor.fetchone()
            try:
                self.grants.associate(cursor, request.deposit_grant_id, message["id"], case_id)
            except DepositGrantError as error:
                raise SmsError(error.code, 409, "Deposit link is already used") from error
            if self.gateway is not None and getattr(self.gateway, "simulated", False):
                sid = self.gateway.send(message_id=str(message["id"]), recipient=recipient,
                                        body=content.body, deposit_url=request.deposit_url)
                cursor.execute("""update public.case_messages
                                  set provider_message_id = %s, status = 'delivered', updated_at = now(),
                                      sent_at = now(), delivered_at = now()
                                  where id = %s returning *""", (sid, message["id"]))
                message = cursor.fetchone()
            cursor.execute("""update public.cases set state_version = state_version + 1,
                              updated_at = now() where id = %s returning state_version""", (case_id,))
            new_version = cursor.fetchone()["state_version"]
            cursor.execute("""insert into public.audit_events
                          (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                           content_revision_before, content_revision_after, metadata_json)
                          values (%s, %s, 'case.whatsapp_created', %s, %s, %s, %s, %s)""",
                           (case_id, actor_id, snapshot.state_version, new_version,
                            snapshot.content_revision, snapshot.content_revision,
                            Jsonb({"message_id": str(message["id"]), "mode": "mock", "status": message["status"],
                                   "recipient_source": request.recipient_confirmation.kind,
                                   "recipient_correction_reason": request.recipient_confirmation.reason.strip()
                                   if request.recipient_confirmation.kind == "manager_correction" else None})))
        return SmsCreateResponse(message=_message_view(message), replayed=False, state_version=new_version)

    def dispatch_fake(self, actor_id: UUID, case_id: UUID, message_id: UUID) -> SmsMessageView:
        """Make a queued fake message visible in the private browser phone."""
        if self.gateway is None or not getattr(self.gateway, "simulated", False):
            raise SmsError("fake_whatsapp_not_configured", 503, "Fake WhatsApp is unavailable")
        with self._connection() as connection, connection.cursor() as cursor:
            self._snapshot(cursor, actor_id, case_id)
            cursor.execute("""select * from public.case_messages
                              where id = %s and case_id = %s and channel = 'whatsapp'
                                and mode = 'mock' and provider = %s and status = 'queued' for update""",
                           (message_id, case_id, self.provider))
            message = cursor.fetchone()
            if message is None:
                raise SmsError("dispatch_not_available", 409, "Fake message is already dispatched")
            link = _deposit_url(message["body"])
            if link is None:
                raise SmsError("deposit_link_missing", 409, "WhatsApp message has no deposit link")
            sid = self.gateway.send(message_id=str(message_id), recipient=message["recipient"],
                                    body=message["body"], deposit_url=link)
            cursor.execute("""update public.case_messages
                              set provider_message_id = %s, status = 'delivered', updated_at = now(),
                                  sent_at = now(), delivered_at = now()
                              where id = %s returning *""", (sid, message_id))
            return _message_view(cursor.fetchone())

    def list_inbound(self, actor_id: UUID, case_id: UUID) -> list[WhatsAppInboundView]:
        with self._connection() as connection, connection.cursor() as cursor:
            self._snapshot(cursor, actor_id, case_id)
            cursor.execute("""select id, body, media_count, evidence_id, received_at
                              from public.case_whatsapp_inbound where case_id = %s
                              order by received_at, id""", (case_id,))
            return [WhatsAppInboundView.model_validate(row) for row in cursor.fetchall()]

    def mock_transition(self, actor_id: UUID, case_id: UUID, message_id: UUID,
                        request: SmsMockTransitionRequest) -> SmsMockTransitionResponse:
        allowed = {
            "queued": {"accepted", "failed", "unknown"},
            "accepted": {"sent", "failed", "unknown"},
            "sent": {"delivered", "failed", "unknown"},
            "unknown": {"accepted", "sent", "delivered", "failed"},
        }
        with self._connection() as connection, connection.cursor() as cursor:
            snapshot = self._snapshot(cursor, actor_id, case_id, lock=True)
            cursor.execute("""select * from public.case_messages
                              where id = %s and case_id = %s and channel = 'whatsapp' and mode = 'mock' for update""",
                           (message_id, case_id))
            message = cursor.fetchone()
            if message is None:
                raise SmsError("message_not_found", 404, "Mock message not found")
            if message["status"] == request.status:
                return SmsMockTransitionResponse(message=_message_view(message),
                                                 state_version=snapshot.state_version, replayed=True)
            if snapshot.state_version != request.expected_state_version:
                raise SmsError("stale_case", 409, "Case changed before delivery update",
                               {"current_state_version": snapshot.state_version})
            if request.status not in allowed.get(message["status"], set()):
                raise SmsError("invalid_transition", 409, "Mock delivery transition is invalid")
            if request.error_code and request.status not in {"failed", "unknown"}:
                raise SmsError("invalid_error_code", 422, "Error code needs failed or unknown status")
            error_code = request.error_code or ("mock_failed" if request.status == "failed" else
                                                "mock_unknown" if request.status == "unknown" else None)
            cursor.execute("""update public.case_messages set status = %s,
                              provider_message_id = coalesce(provider_message_id, %s),
                              error_code = %s, updated_at = now(),
                              sent_at = case when %s in ('sent', 'delivered')
                                  then coalesce(sent_at, now()) else sent_at end,
                              delivered_at = case when %s = 'delivered'
                                  then now() else delivered_at end
                              where id = %s returning *""",
                           (request.status, f"mock:{message_id}", error_code,
                            request.status, request.status, message_id))
            updated = cursor.fetchone()
            cursor.execute("""update public.cases set state_version = state_version + 1,
                              updated_at = now() where id = %s returning state_version""", (case_id,))
            new_version = cursor.fetchone()["state_version"]
            cursor.execute("""insert into public.audit_events
                          (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                           content_revision_before, content_revision_after, metadata_json)
                          values (%s, %s, 'case.whatsapp_status_changed', %s, %s, %s, %s, %s)""",
                           (case_id, actor_id, snapshot.state_version, new_version,
                            snapshot.content_revision, snapshot.content_revision,
                            Jsonb({"message_id": str(message_id), "from": message["status"],
                                   "to": request.status, "mode": "mock"})))
        return SmsMockTransitionResponse(message=_message_view(updated),
                                         state_version=new_version, replayed=False)

    def list_messages(self, actor_id: UUID, case_id: UUID) -> list[SmsMessageView]:
        with self._connection() as connection, connection.cursor() as cursor:
            self._snapshot(cursor, actor_id, case_id)
            cursor.execute("""select * from public.case_messages where case_id = %s and channel = 'whatsapp'
                              order by created_at desc, id desc""", (case_id,))
            return [_message_view(row) for row in cursor.fetchall()]

    def link(self, actor_id: UUID, case_id: UUID, message_id: UUID) -> SmsLinkResponse:
        with self._connection() as connection, connection.cursor() as cursor:
            self._snapshot(cursor, actor_id, case_id)
            cursor.execute("""select m.body, g.expires_at, g.revoked_at from public.case_messages m
                              join public.deposit_grants g on g.message_id = m.id and g.case_id = m.case_id
                              where m.id = %s and m.case_id = %s and m.channel = 'whatsapp'""", (message_id, case_id))
            row = cursor.fetchone()
        if row is None:
            raise SmsError("message_not_found", 404, "WhatsApp message not found")
        if row["revoked_at"] is not None or row["expires_at"] <= datetime.now(timezone.utc):
            raise SmsError("deposit_link_expired", 409, "The deposit link is no longer active")
        prefix = "Accès sécurisé à votre dossier : "
        line = next((line for line in row["body"].splitlines() if line.startswith(prefix)), None)
        if line is None:
            raise SmsError("deposit_link_missing", 409, "WhatsApp message has no deposit link")
        return SmsLinkResponse(url=line[len(prefix):], expires_at=row["expires_at"])
