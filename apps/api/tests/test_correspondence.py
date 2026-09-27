import json
from datetime import datetime, timezone
from io import BytesIO
from urllib.error import HTTPError, URLError
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from claim_api.case_service import CaseNotFoundError
from claim_api.correspondence import (
    ResendMailer,
    CorrespondenceError,
    CorrespondenceService,
    CameraMailRequest,
    EditCorrespondence,
    SendCorrespondence,
    email_address,
)
from test_alembic_version import _test_database_url
from test_sms_service import sms_db


def test_resend_uses_fixed_origin_text_payload_and_stable_idempotency_key():
    calls = []

    def transport(request, timeout):
        calls.append(request)
        return BytesIO(b'{"id":"accepted-1"}')

    mailer = ResendMailer(
        api_key="test-key",
        sender="claims@company.fr",
        reply_to="replies@company.fr",
        open_url=transport,
    )
    payload = mailer.payload(
        {"recipient": "camera@city.fr", "subject": "Demande", "body": "Bonjour"}
    )
    assert mailer.send(payload, "stable-key") == "accepted-1"
    assert calls[0].full_url == "https://api.resend.com/emails"
    assert calls[0].get_header("Idempotency-key") == "stable-key"
    assert json.loads(calls[0].data)["reply_to"] == "replies@company.fr"
    assert "attachments" not in json.loads(calls[0].data)


@pytest.mark.parametrize(
    "address",
    [
        "a@city.fr\r\nBcc: leak@example.com",
        "a@city.fr,b@city.fr",
        "https://city.fr",
        "",
    ],
)
def test_email_recipient_cannot_inject_other_recipients(address):
    with pytest.raises(ValueError):
        email_address(address)


def test_timeout_is_unknown_not_success_and_400_is_definitive_failure():
    for error, uncertain in [
        (URLError("timeout"), True),
        (HTTPError("https://api.resend.com/emails", 400, "bad", {}, None), False),
    ]:

        def fail(*args, **kwargs):
            raise error

        mailer = ResendMailer(
            api_key="test",
            sender="claims@company.fr",
            reply_to="claims@company.fr",
            open_url=fail,
        )
        with pytest.raises(CorrespondenceError) as caught:
            mailer.send({}, "id")
        assert caught.value.uncertain == uncertain


class Mailer:
    configured = True

    def __init__(self):
        self.calls = []
        self.uncertain = False

    def payload(self, row):
        return {
            "to": [row["recipient"]],
            "text": row["body"],
            "subject": row["subject"],
        }

    def send(self, payload, key):
        self.calls.append((payload, key))
        if self.uncertain:
            raise CorrespondenceError("email_delivery_unknown", uncertain=True)
        return "email-1"


def setup(sms_db):
    engine, _, actor, outsider, case_id, _ = sms_db
    with engine.begin() as conn:
        conn.execute(
            text(
                """update public.cases set intake_json=intake_json ||
            '{"location":"Rue Faidherbe, Lille","incident_at":"2026-09-25T12:00:00Z"}'::jsonb where id=:id"""
            ),
            {"id": case_id},
        )
    mailer = Mailer()
    service = CorrespondenceService(_test_database_url(), mailer)
    request = CameraMailRequest(
        expected_state_version=1,
        camera_label="Caméra carrefour",
        controller="Ville de Lille",
        recipient="camera@city.fr",
        source_url="https://city.fr/contact",
        recipient_confirmed=True,
    )
    return engine, service, mailer, actor, outsider, case_id, request


def test_cctv_draft_is_reviewable_owned_and_sends_only_once(sms_db):
    _, service, mailer, actor, outsider, case_id, request = setup(sms_db)
    with pytest.raises(CaseNotFoundError):
        service.camera_draft(outsider, case_id, request)
    draft = service.camera_draft(actor, case_id, request)
    assert (
        mailer.calls == []
        and "11:55:00" in draft["body"]
        and "12:05:00" in draft["body"]
    )
    with pytest.raises(CorrespondenceError):
        service.send(
            actor,
            case_id,
            draft["id"],
            SendCorrespondence(version=1, confirm_send=False),
        )
    sent = service.send(
        actor, case_id, draft["id"], SendCorrespondence(version=1, confirm_send=True)
    )
    assert sent["status"] == "sent" and sent["provider_message_id"] == "email-1"
    service.send(
        actor, case_id, draft["id"], SendCorrespondence(version=1, confirm_send=True)
    )
    assert len(mailer.calls) == 1
    with pytest.raises(CaseNotFoundError):
        service.send(
            outsider,
            case_id,
            draft["id"],
            SendCorrespondence(version=1, confirm_send=True),
        )


def test_unknown_delivery_reuses_immutable_payload_then_blocks_after_window(sms_db):
    engine, service, mailer, actor, _, case_id, request = setup(sms_db)
    draft = service.camera_draft(actor, case_id, request)
    mailer.uncertain = True
    first = service.send(
        actor, case_id, draft["id"], SendCorrespondence(version=1, confirm_send=True)
    )
    assert first["status"] == "unknown"
    with pytest.raises(CorrespondenceError):
        service.edit(
            actor,
            case_id,
            draft["id"],
            EditCorrespondence(
                version=1, recipient="other@city.fr", subject="changed", body="changed"
            ),
        )
    service.send(
        actor, case_id, draft["id"], SendCorrespondence(version=1, confirm_send=True)
    )
    assert mailer.calls[0] == mailer.calls[1]
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.case_correspondence set send_started_at=now()-interval '24 hours' where id=:id"
            ),
            {"id": draft["id"]},
        )
    with pytest.raises(CorrespondenceError, match="reconciliation"):
        service.send(
            actor,
            case_id,
            draft["id"],
            SendCorrespondence(version=1, confirm_send=True),
        )
    assert len(mailer.calls) == 2


def test_email_rejects_stale_case_and_stale_preview(sms_db):
    engine, service, mailer, actor, _, case_id, request = setup(sms_db)
    draft = service.camera_draft(actor, case_id, request)
    service.edit(
        actor,
        case_id,
        draft["id"],
        EditCorrespondence(
            version=1, recipient="other@city.fr", subject="Question", body="Bonjour"
        ),
    )
    with pytest.raises(CorrespondenceError, match="changed"):
        service.send(
            actor,
            case_id,
            draft["id"],
            SendCorrespondence(version=1, confirm_send=True),
        )
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.cases set content_revision=content_revision+1 where id=:id"
            ),
            {"id": case_id},
        )
    with pytest.raises(CorrespondenceError, match="stale"):
        service.send(
            actor,
            case_id,
            draft["id"],
            SendCorrespondence(version=2, confirm_send=True),
        )
    assert mailer.calls == []
