"""Editable CCTV/garage drafts and explicit, idempotent Resend delivery."""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb
from pydantic import Field, field_validator

from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.models import ContractModel
from claim_api.postgres_cases import PostgresCaseRepository


class CorrespondenceError(ValueError):
    def __init__(self, code, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


def email_address(value):
    value = value.strip()
    if len(value) > 254 or not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}",
        value,
    ):
        raise ValueError("Une adresse email unique et valide est requise.")
    return value


class CameraMailRequest(ContractModel):
    expected_state_version: int = Field(ge=1)
    camera_label: str = Field(min_length=3, max_length=250)
    controller: str = Field(min_length=3, max_length=250)
    recipient: str = Field(min_length=3, max_length=254)
    source_url: str = Field(min_length=10, max_length=2000)
    recipient_confirmed: bool

    _email = field_validator("recipient")(email_address)

    @field_validator("source_url")
    @classmethod
    def public_reference(cls, value):
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
        ):
            raise ValueError("Indiquez la page publique du responsable de caméra.")
        return value


class EditCorrespondence(ContractModel):
    version: int = Field(ge=1)
    recipient: str = Field(max_length=254)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=12000)

    @field_validator("recipient")
    @classmethod
    def email_or_empty(cls, value):
        return email_address(value) if value.strip() else ""

    @field_validator("subject")
    @classmethod
    def one_line(cls, value):
        if "\r" in value or "\n" in value or not value.strip():
            raise ValueError("Objet invalide")
        return value.strip()


class SendCorrespondence(ContractModel):
    version: int = Field(ge=1)
    confirm_send: bool


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ResendMailer:
    def __init__(self, api_key=None, sender=None, reply_to=None, open_url=None):
        self.api_key = (
            api_key if api_key is not None else os.getenv("RESEND_API_KEY", "")
        )
        self.sender = (
            sender if sender is not None else os.getenv("CLAIMROOM_EMAIL_FROM", "")
        )
        self.reply_to = (
            reply_to
            if reply_to is not None
            else os.getenv("CLAIMROOM_EMAIL_REPLY_TO", "")
        )
        self.open_url = open_url or build_opener(NoRedirect()).open

    @property
    def configured(self):
        try:
            email_address(self.sender)
            email_address(self.reply_to)
            return bool(self.api_key)
        except ValueError:
            return False

    def payload(self, row):
        if not self.configured:
            raise CorrespondenceError("email_not_configured")
        recipient = email_address(row["recipient"])
        if (
            recipient.rsplit("@", 1)[1]
            .lower()
            .endswith((".test", ".invalid", ".example"))
        ):
            raise CorrespondenceError("email_demo_recipient")
        return {
            "from": self.sender,
            "to": [recipient],
            "reply_to": self.reply_to,
            "subject": row["subject"],
            "text": row["body"],
        }

    def send(self, payload, key):
        if not self.configured:
            raise CorrespondenceError("email_not_configured")
        request = Request(
            "https://api.resend.com/emails",
            method="POST",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": key,
                "User-Agent": "Claimroom/1.0",
            },
        )
        try:
            with self.open_url(request, timeout=25) as response:
                result = json.loads(response.read(100000))
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("id"), str)
                or not result["id"]
            ):
                raise ValueError("missing delivery id")
            return result["id"]
        except HTTPError as error:
            raise CorrespondenceError(
                "email_rate_limited" if error.code == 429 else "email_rejected",
                uncertain=error.code >= 500,
            ) from None
        except (URLError, OSError, TimeoutError, ValueError, KeyError):
            raise CorrespondenceError(
                "email_delivery_unknown", uncertain=True
            ) from None


class CorrespondenceService(PostgresCaseRepository):
    def __init__(self, database_url, mailer=None):
        super().__init__(database_url)
        self.mailer = mailer or ResendMailer()

    @staticmethod
    def _owned(conn, actor, case_id):
        row = conn.execute(
            "select * from public.cases where id=%s and created_by_user_id=%s for update",
            (case_id, actor),
        ).fetchone()
        if row is None:
            raise CaseNotFoundError
        return row

    def camera_draft(self, actor, case_id, request):
        case = self.get_case(actor, case_id)
        if case is None:
            raise CaseNotFoundError
        if case.state_version != request.expected_state_version:
            raise StaleCaseError(case.state_version)
        if not request.recipient_confirmed:
            raise CorrespondenceError("camera_recipient_unconfirmed")
        if not case.intake.location or not case.intake.incident_at:
            raise CorrespondenceError("camera_location_time_required")
        instant = case.intake.incident_at
        start = (instant - timedelta(minutes=5)).isoformat()
        end = (instant + timedelta(minutes=5)).isoformat()
        subject = f"Demande de conservation d’images — {case.intake.location[:180]}"
        body = (
            f"Bonjour,\n\nNous vous contactons en tant que responsable identifié : {request.controller}.\n"
            f"Un accident a été déclaré à {case.intake.location}, le {instant.isoformat()}.\n"
            f"Caméra concernée : {request.camera_label}.\nCréneau demandé : du {start} au {end} (décalage horaire indiqué).\n\n"
            "Pourriez-vous nous confirmer si cette caméra couvre la scène et si des images sont disponibles ? "
            "Nous sollicitons leur conservation, si possible, et vous remercions de préciser la procédure, "
            "les justificatifs et les autorisations nécessaires à une éventuelle communication.\n\n"
            "Merci de répondre à cet email avant toute transmission de fichiers.\n"
            f"Référence du dossier : {str(case_id)[:8]}.\nSource du contact : {request.source_url}\n\nCordialement,\nClaimroom"
        )
        if case.synthetic:
            subject = "[Démonstration] " + subject
            body = (
                "Dossier fictif de démonstration : merci de ne pas communiquer d’images réelles pour ce test.\n\n"
                + body
            )
        with self._connection() as conn:
            current = self._owned(conn, actor, case_id)
            if current["state_version"] != request.expected_state_version:
                raise StaleCaseError(current["state_version"])
            return conn.execute(
                """insert into public.case_correspondence
                (case_id,kind,content_revision,recipient,subject,body,source_url)
                values (%s,'cctv',%s,%s,%s,%s,%s) returning *""",
                (
                    case_id,
                    case.content_revision,
                    request.recipient,
                    subject,
                    body,
                    request.source_url,
                ),
            ).fetchone()

    def edit(self, actor, case_id, draft_id, request):
        with self._connection() as conn:
            case = self._owned(conn, actor, case_id)
            row = conn.execute(
                "select * from public.case_correspondence where id=%s and case_id=%s for update",
                (draft_id, case_id),
            ).fetchone()
            if not row:
                raise CaseNotFoundError
            if (
                row["status"] not in {"draft", "failed"}
                or row["version"] != request.version
            ):
                raise CorrespondenceError("email_draft_changed")
            if row["content_revision"] != case["content_revision"]:
                raise CorrespondenceError("email_draft_stale")
            return conn.execute(
                """update public.case_correspondence set recipient=%s,subject=%s,body=%s,
                version=version+1,status='draft',send_payload=null,send_started_at=null,error_code=null,updated_at=now()
                where id=%s returning *""",
                (request.recipient, request.subject, request.body, draft_id),
            ).fetchone()

    def send(self, actor, case_id, draft_id, request):
        if not request.confirm_send:
            raise CorrespondenceError("email_confirmation_required")
        with self._connection() as conn:
            case = self._owned(conn, actor, case_id)
            row = conn.execute(
                "select * from public.case_correspondence where id=%s and case_id=%s for update",
                (draft_id, case_id),
            ).fetchone()
            if not row:
                raise CaseNotFoundError
            if row["version"] != request.version:
                raise CorrespondenceError("email_draft_changed")
            if row["status"] == "sent":
                return row
            if row["content_revision"] != case["content_revision"]:
                raise CorrespondenceError("email_draft_stale")
            started = row["send_started_at"]
            if started:
                age = (datetime.now(timezone.utc) - started).total_seconds()
                if age >= 23 * 3600:
                    raise CorrespondenceError("email_reconciliation_required")
                if row["status"] == "sending" and age < 60:
                    raise CorrespondenceError("email_in_progress")
            payload = row["send_payload"] or self.mailer.payload(row)
            conn.execute(
                """update public.case_correspondence set status='sending',send_payload=%s,
                send_started_at=coalesce(send_started_at,now()),error_code=null,updated_at=now() where id=%s""",
                (Jsonb(payload), draft_id),
            )
        try:
            provider_id = self.mailer.send(
                payload, f"claimroom/{draft_id}/{request.version}"
            )
            status, error = "sent", None
        except CorrespondenceError as exc:
            provider_id = None
            status, error = ("unknown" if exc.uncertain else "failed"), exc.code
        with self._connection() as conn:
            # Do not turn a previously confirmed acceptance into a failure after a concurrent retry.
            conn.execute(
                """update public.case_correspondence set status=%s,provider_message_id=%s,
                error_code=%s,updated_at=now() where id=%s and version=%s and status<>'sent'""",
                (status, provider_id, error, draft_id, request.version),
            )
            return conn.execute(
                "select * from public.case_correspondence where id=%s", (draft_id,)
            ).fetchone()
