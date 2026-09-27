"""One-way SMS invitation to the private deposit conversation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field
from twilio.base.exceptions import TwilioRestException
from twilio.http.http_client import TwilioHttpClient
from twilio.request_validator import RequestValidator
from twilio.rest import Client

from claim_api.deposit_grants import DepositGrantError
from claim_api.sms_models import RecipientConfirmation, SmsMessageView, SmsPreviewRequest
from claim_api.sms_recipient import mask_e164, validate_e164
from claim_api.sms_service import CaseMessageService, SmsError


PROVIDER = "twilio.sms.claimroom"
SERVICE_SID = "MG77be4e6c8d35a9423917e9ad99ff2c9c"
_LINK = re.compile(r"https://[^\s]+#token=[A-Za-z0-9_-]+")
_FRENCH_MOBILE = re.compile(r"^\+33[67][0-9]{8}$")


class SmsLinkModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SmsLinkCreateRequest(SmsLinkModel):
    expected_state_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    recipient_confirmation: RecipientConfirmation
    recipient_consent_confirmed: bool
    deposit_grant_id: UUID
    deposit_url: str = Field(min_length=20, max_length=2048)
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SmsLinkCreateResponse(SmsLinkModel):
    message: SmsMessageView
    replayed: bool
    state_version: int


@dataclass(frozen=True)
class TwilioSmsConfig:
    account_sid: str
    auth_token: str
    messaging_service_sid: str
    webhook_base_url: str

    @classmethod
    def from_env(cls) -> "TwilioSmsConfig":
        mode = os.getenv("SMS_LINK_DELIVERY_MODE", "disabled").strip().lower()
        sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        service = os.getenv("TWILIO_SMS_MESSAGING_SERVICE_SID", "").strip()
        base = os.getenv("TWILIO_SMS_WEBHOOK_BASE_URL", "").strip().rstrip("/")
        parsed = urlsplit(base)
        if mode != "twilio" or not re.fullmatch(r"AC[0-9a-fA-F]{32}", sid) or not token:
            raise ValueError("SMS invitation is not configured")
        if service != SERVICE_SID:
            raise ValueError("Unexpected Claimroom messaging service")
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or
                parsed.query or parsed.fragment or not parsed.path.endswith("/v1/webhooks/twilio/sms")):
            raise ValueError("Invalid SMS callback base URL")
        return cls(sid, token, service, base)

    def callback_url(self, message_id: UUID) -> str:
        return f"{self.webhook_base_url}/status?message_id={message_id}"

    def valid_callback(self, url: str, params: dict[str, str], signature: str) -> bool:
        return bool(signature) and RequestValidator(self.auth_token).validate(url, params, signature)


class TwilioSmsGateway:
    def __init__(self, config: TwilioSmsConfig, client: Client | None = None):
        self.config = config
        self.client = client or Client(config.account_sid, config.auth_token,
                                       http_client=TwilioHttpClient(timeout=8))

    def send(self, message_id: UUID, recipient: str, body: str) -> str:
        try:
            result = self.client.messages.create(
                messaging_service_sid=self.config.messaging_service_sid,
                to=validate_e164(recipient), body=body,
                status_callback=self.config.callback_url(message_id),
            )
        except TwilioRestException as error:
            if error.status < 500:
                raise SmsError(f"twilio_{error.code or error.status}", 409, "SMS rejected by Twilio") from error
            raise SmsError("twilio_unavailable", 503, "SMS send status is uncertain") from error
        except Exception as error:
            raise SmsError("twilio_unavailable", 503, "SMS send status is uncertain") from error
        if not isinstance(result.sid, str) or not re.fullmatch(r"(?:SM|MM)[0-9a-fA-F]{32}", result.sid):
            raise SmsError("twilio_missing_sid", 503, "SMS send status is uncertain")
        return result.sid


def _body(url: str) -> str:
    return f"CLAIMROOM : accedez a votre dossier, envoyez vos photos et ecrivez-nous ici : {url} (pas de reponse par SMS)"


def _digest(recipient: str, body: str, grant_id: UUID, revision: int) -> str:
    payload = {"channel": "sms", "provider": PROVIDER, "mode": "live", "recipient": recipient,
               "body": body, "grant_id": str(grant_id), "content_revision": revision}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _view(row: dict) -> SmsMessageView:
    return SmsMessageView(
        id=row["id"], case_id=row["case_id"], recipient_masked=mask_e164(row["recipient"]),
        body_preview=_LINK.sub("[lien masqué]", row["body"]), mode="live", provider=PROVIDER,
        status=row["status"], error_code=row["error_code"], created_at=row["created_at"],
        updated_at=row["updated_at"], sent_at=row["sent_at"], delivered_at=row["delivered_at"],
    )


class SmsLinkService(CaseMessageService):
    def __init__(self, database_url: str, grants, caller_lookup=None,
                 gateway: TwilioSmsGateway | None = None):
        super().__init__(database_url, grants, caller_lookup)
        self.sms_gateway = gateway

    def auto_follow_up(self, actor_id: UUID, case_id: UUID) -> SmsMessageView:
        """Send one private link after a non-urgent inbound call has ended."""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))",(f"auto-sms:{case_id}",))
            snapshot = self._snapshot(cursor, actor_id, case_id)
            if snapshot.voice_status not in {"complete", "incomplete"} or not snapshot.call_id:
                raise SmsError("call_not_ended", 409, "L'appel doit être terminé avant le SMS.")
            cursor.execute("""select 1 from public.voice_sessions s
                              join public.voice_session_events e on e.session_id=s.id
                              where s.case_id=%s and s.provider_call_id=%s
                                and e.event_type='end_of_call_report' limit 1""",
                           (case_id, snapshot.call_id))
            if cursor.fetchone() is None:
                raise SmsError("call_not_ended", 409, "L'appel doit être terminé avant le SMS.")
            key = f"auto-deposit:{snapshot.call_id}"
            # A manager may already have sent the link while the webhook was
            # finishing. That SMS fulfils the same invitation; never send two.
            cursor.execute("""select * from public.case_messages where case_id=%s
                              and channel='sms' and provider=%s
                              order by created_at, id limit 1""", (case_id, PROVIDER))
            existing = cursor.fetchone()
            if existing:
                return self.dispatch(actor_id, case_id, existing['id']) if (existing['idempotency_key'] == key
                    and existing['status'] == 'queued') else _view(existing)
            confirmation = RecipientConfirmation(kind='caller_id')
            preview = self.preview_sms_link(actor_id,case_id,SmsPreviewRequest(
                expected_state_version=snapshot.state_version,recipient_confirmation=confirmation))
            result = self.create_sms_link(actor_id,case_id,SmsLinkCreateRequest(
                expected_state_version=snapshot.state_version,idempotency_key=key,
                recipient_confirmation=confirmation,recipient_consent_confirmed=True,
                deposit_grant_id=preview['deposit_grant_id'],deposit_url=preview['deposit_url'],preview_hash=preview['preview_hash']))
            return self.dispatch(actor_id,case_id,result.message.id)

    @staticmethod
    def _french_mobile(recipient: str) -> str:
        value = validate_e164(recipient)
        if not _FRENCH_MOBILE.fullmatch(value):
            raise SmsError("unsupported_recipient", 422, "Use a French mobile number")
        return value

    def preview_sms_link(self, actor_id: UUID, case_id: UUID, request: SmsPreviewRequest) -> dict:
        with self._connection() as connection, connection.cursor() as cursor:
            snapshot = self._snapshot(cursor, actor_id, case_id)
        if snapshot.state_version != request.expected_state_version:
            raise SmsError("stale_case", 409, "Case changed before preview")
        warnings = self._check_ready(snapshot)
        recipient = self._french_mobile(self._recipient(snapshot, request.recipient_confirmation))
        grant = self.grants.issue(case_id, snapshot.content_revision, actor_id=actor_id,
                                  expected_state_version=request.expected_state_version)
        body = _body(grant.url)
        return {"recipient_masked": mask_e164(recipient), "body": body,
                "deposit_url": grant.url, "deposit_grant_id": grant.grant_id,
                "deposit_link_expires_at": grant.expires_at, "content_revision": snapshot.content_revision,
                "preview_hash": _digest(recipient, body, grant.grant_id, snapshot.content_revision),
                "warnings": warnings}

    def create_sms_link(self, actor_id: UUID, case_id: UUID,
                        request: SmsLinkCreateRequest) -> SmsLinkCreateResponse:
        if not request.recipient_consent_confirmed:
            raise SmsError("recipient_consent_required", 422, "Confirm recipient consent before sending")
        if self.sms_gateway is None:
            raise SmsError("sms_not_configured", 503, "SMS sending is unavailable")
        with self._connection() as connection, connection.cursor() as cursor:
            prior = self._snapshot(cursor, actor_id, case_id)
        recipient = self._french_mobile(self._recipient(prior, request.recipient_confirmation))
        with self._connection() as connection, connection.cursor() as cursor:
            snapshot = self._snapshot(cursor, actor_id, case_id, lock=True)
            cursor.execute("""select * from public.case_messages where case_id=%s and channel='sms'
                              and idempotency_key=%s""", (case_id, request.idempotency_key))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("""select 1 from public.deposit_grants where id=%s and case_id=%s
                                  and message_id=%s""", (request.deposit_grant_id, case_id, existing["id"]))
                if (existing["provider"] != PROVIDER or existing["payload_hash"] != request.preview_hash or
                        existing["recipient"] != recipient or cursor.fetchone() is None or
                        request.deposit_url not in existing["body"]):
                    raise SmsError("idempotency_conflict", 409, "Idempotency key has another payload")
                return SmsLinkCreateResponse(message=_view(existing), replayed=True,
                                             state_version=snapshot.state_version)
            self._check_ready(snapshot)
            if snapshot.call_id != prior.call_id or snapshot.state_version != request.expected_state_version:
                raise SmsError("stale_case", 409, "Case changed before SMS creation")
            body = _body(request.deposit_url)
            if _digest(recipient, body, request.deposit_grant_id, snapshot.content_revision) != request.preview_hash:
                raise SmsError("stale_preview", 409, "SMS preview changed")
            try:
                self.grants.validate_url(cursor, request.deposit_grant_id, case_id, request.deposit_url,
                                         snapshot.content_revision)
            except DepositGrantError as error:
                raise SmsError(error.code, 409, "Deposit link is invalid or expired") from error
            cursor.execute("""insert into public.case_messages
                (case_id, channel, mode, recipient, body, status, provider, idempotency_key, payload_hash)
                values (%s,'sms','live',%s,%s,'queued',%s,%s,%s) returning *""",
                (case_id, recipient, body, PROVIDER, request.idempotency_key, request.preview_hash))
            message = cursor.fetchone()
            self.grants.associate(cursor, request.deposit_grant_id, message["id"], case_id)
            cursor.execute("""update public.cases set state_version=state_version+1,
                              updated_at=now() where id=%s returning state_version""", (case_id,))
            version = cursor.fetchone()["state_version"]
            cursor.execute("""insert into public.audit_events
                (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                 content_revision_before, content_revision_after, metadata_json)
                values (%s,%s,'case.sms_link_created',%s,%s,%s,%s,%s)""",
                (case_id, actor_id, snapshot.state_version, version, snapshot.content_revision,
                 snapshot.content_revision, Jsonb({"message_id": str(message["id"]), "status": "queued",
                                                   "consent_confirmed": True})))
        return SmsLinkCreateResponse(message=_view(message), replayed=False, state_version=version)

    def dispatch(self, actor_id: UUID, case_id: UUID, message_id: UUID) -> SmsMessageView:
        if self.sms_gateway is None:
            raise SmsError("sms_not_configured", 503, "SMS sending is unavailable")
        with self._connection() as connection, connection.cursor() as cursor:
            self._snapshot(cursor, actor_id, case_id)
            cursor.execute("""update public.case_messages set status='unknown', updated_at=now()
                where id=%s and case_id=%s and channel='sms' and provider=%s and status='queued'
                returning *""", (message_id, case_id, PROVIDER))
            message = cursor.fetchone()
        if message is None:
            raise SmsError("dispatch_not_available", 409, "SMS send was already attempted")
        sid, status, error_code = None, "accepted", None
        try:
            sid = self.sms_gateway.send(message_id, message["recipient"], message["body"])
        except SmsError as error:
            status = "failed" if error.code.startswith("twilio_") and error.status_code == 409 else "unknown"
            error_code = error.code
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("""update public.case_messages set
                provider_message_id=coalesce(provider_message_id,%s),
                status=case when status in ('sent','delivered','failed') then status else %s end,
                error_code=case when status in ('sent','delivered','failed') then error_code else %s end,
                updated_at=now() where id=%s and case_id=%s and channel='sms' and provider=%s returning *""",
                (sid, status, error_code, message_id, case_id, PROVIDER))
            return _view(cursor.fetchone())

    def apply_status(self, message_id: UUID, sid: str, recipient: str,
                     status: str, error_code: str | None) -> bool:
        mapped = {"queued": "accepted", "accepted": "accepted", "sending": "accepted",
                  "sent": "sent", "delivered": "delivered", "failed": "failed",
                  "undelivered": "failed"}.get(status)
        if mapped is None:
            return False
        ranks = {"queued": 0, "unknown": 0, "accepted": 1, "sent": 2, "delivered": 3, "failed": 3}
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("""select * from public.case_messages where id=%s and channel='sms'
                              and provider=%s for update""", (message_id, PROVIDER))
            row = cursor.fetchone()
            if row is None or row["recipient"] != recipient or (row["provider_message_id"] and row["provider_message_id"] != sid):
                return False
            if ranks[mapped] <= ranks[row["status"]]:
                return True
            cursor.execute("""update public.case_messages set provider_message_id=coalesce(provider_message_id,%s),
                status=%s, error_code=%s, updated_at=now(),
                sent_at=case when %s in ('sent','delivered') then coalesce(sent_at,now()) else sent_at end,
                delivered_at=case when %s='delivered' then now() else delivered_at end where id=%s""",
                (sid, mapped, error_code if mapped == "failed" else None,
                 mapped, mapped, message_id))
        return True

    def list_links(self, actor_id: UUID, case_id: UUID) -> list[SmsMessageView]:
        with self._connection() as connection, connection.cursor() as cursor:
            self._snapshot(cursor, actor_id, case_id)
            cursor.execute("""select * from public.case_messages where case_id=%s and channel='sms'
                              and provider=%s order by created_at desc, id desc""", (case_id, PROVIDER))
            return [_view(row) for row in cursor.fetchall()]

    def link(self, actor_id: UUID, case_id: UUID, message_id: UUID) -> dict:
        with self._connection() as connection, connection.cursor() as cursor:
            self._snapshot(cursor, actor_id, case_id)
            cursor.execute("""select m.body, g.expires_at, g.revoked_at from public.case_messages m
                join public.deposit_grants g on g.message_id=m.id and g.case_id=m.case_id
                where m.id=%s and m.case_id=%s and m.channel='sms' and m.provider=%s""",
                (message_id, case_id, PROVIDER))
            row = cursor.fetchone()
        if row is None:
            raise SmsError("message_not_found", 404, "SMS not found")
        if row["revoked_at"] is not None or row["expires_at"] <= datetime.now(timezone.utc):
            raise SmsError("deposit_link_expired", 409, "Deposit link has expired")
        match = _LINK.search(row["body"])
        if match is None:
            raise SmsError("deposit_link_missing", 409, "SMS has no deposit link")
        return {"url": match.group(0), "expires_at": row["expires_at"]}
