"""Real SMS invitation and deposit chat contract without a live Twilio send."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.insured_portal import InsuredPortalService
from claim_api.main import create_app
from claim_api.portal_chat import PortalChatService
from claim_api.routes.sms_link import get_chat_service, get_sms_link_service
from claim_api.sms_link import SmsLinkService, TwilioSmsConfig, TwilioSmsGateway
from claim_api.sms_models import RecipientConfirmation, SmsPreviewRequest
from claim_api.sms_service import SmsError
from claim_api.voice_repository import VoiceIntakeRepository
from test_sms_service import sms_db
from test_voice_intake import request as voice_request


NUMBER = "+33612345678"


def _confirmation():
    return RecipientConfirmation(kind="manager_correction", number=NUMBER,
                                 reason="Numéro confirmé par le gestionnaire")


def test_ended_incomplete_call_gets_one_sms_and_manual_send_prevents_duplicate(sms_db):
    _engine, base, actor, _outsider, _first, _second = sms_db
    sent = []

    class Gateway:
        def send(self, message_id, recipient, body):
            sent.append((recipient, body))
            return "SM" + uuid4().hex

    sms = SmsLinkService(base.database_url, base.grants,
                         caller_lookup=SimpleNamespace(candidate=lambda _: NUMBER), gateway=Gateway())
    voice = VoiceIntakeRepository(base.database_url)

    def ended_call():
        request = voice_request(event_type="end_of_call_report", missing={"location"}).model_copy(update={
            "provider_call_id": str(uuid4()), "mode": "live", "telephony_provider": "twilio",
        })
        result = voice.ingest(actor, request)
        assert result.session.triage.status == "incomplete"
        return result.session.case_id

    case_id = ended_call()
    first = sms.auto_follow_up(actor, case_id)
    repeated = sms.auto_follow_up(actor, case_id)
    assert first.id == repeated.id and len(sent) == 1
    assert first.status == "accepted" and sent[0][0] == NUMBER
    assert "#token=" in sent[0][1]

    manual_case = ended_call()
    preview = sms.preview_sms_link(actor, manual_case, SmsPreviewRequest(
        expected_state_version=1, recipient_confirmation=_confirmation()))
    from claim_api.sms_link import SmsLinkCreateRequest
    manual = sms.create_sms_link(actor, manual_case, SmsLinkCreateRequest(
        expected_state_version=1, idempotency_key="manager-send",
        recipient_confirmation=_confirmation(), recipient_consent_confirmed=True,
        deposit_grant_id=preview["deposit_grant_id"], deposit_url=preview["deposit_url"],
        preview_hash=preview["preview_hash"],
    ))
    sms.dispatch(actor, manual_case, manual.message.id)
    assert sms.auto_follow_up(actor, manual_case).id == manual.message.id
    assert len(sent) == 2

    active_request = voice_request(event_type="final_turn").model_copy(update={
        "provider_call_id": str(uuid4()), "mode": "live", "telephony_provider": "twilio",
    })
    active_case = voice.ingest(actor, active_request).session.case_id
    with pytest.raises(SmsError) as error:
        sms.auto_follow_up(actor, active_case)
    assert error.value.code == "call_not_ended" and len(sent) == 2


def test_sms_link_opens_case_scoped_chat_and_accepts_signed_status(sms_db, monkeypatch):
    _engine, base, actor, outsider, case_id, other_case = sms_db
    sent = []
    twilio_sid = "SM" + uuid4().hex

    class Gateway:
        def send(self, message_id, recipient, body):
            sent.append((message_id, recipient, body))
            return twilio_sid

    manager = SmsLinkService(base.database_url, base.grants, gateway=Gateway())
    chat = PortalChatService(base.database_url)
    preview = manager.preview_sms_link(actor, case_id, SmsPreviewRequest(
        expected_state_version=1, recipient_confirmation=_confirmation()))
    assert preview["deposit_url"] in preview["body"]
    assert preview["recipient_masked"].endswith("5678")

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(actor))
    app.dependency_overrides[get_sms_link_service] = lambda: manager
    app.dependency_overrides[get_chat_service] = lambda: chat
    client = TestClient(app)
    request = {"expected_state_version": 1, "idempotency_key": "one", "recipient_confirmation":
               _confirmation().model_dump(), "recipient_consent_confirmed": True,
               "deposit_grant_id": str(preview["deposit_grant_id"]),
               "deposit_url": preview["deposit_url"], "preview_hash": preview["preview_hash"]}
    created = client.post(f"/v1/cases/{case_id}/sms-links", json=request)
    assert created.status_code == 201, created.text
    message = created.json()["message"]
    assert message["status"] == "accepted" and len(sent) == 1
    assert sent[0][1] == NUMBER and preview["deposit_url"] in sent[0][2]
    replay = client.post(f"/v1/cases/{case_id}/sms-links", json=request)
    assert replay.status_code == 200 and replay.json()["replayed"] and len(sent) == 1
    assert preview["deposit_url"] not in client.get(f"/v1/cases/{case_id}/sms-links").text
    assert client.get(f"/v1/cases/{other_case}/sms-links/{message['id']}/link").status_code == 404

    token = preview["deposit_url"].split("#token=", 1)[1]
    session = InsuredPortalService(base.database_url).exchange(token)["session_token"]
    guest_headers = {"Authorization": f"Bearer {session}"}
    summary = client.get("/v1/deposit/summary", headers=guest_headers)
    assert summary.status_code == 200 and summary.json()["conversation_available"] is True
    msg_id = str(uuid4())
    insured_message = client.post("/v1/deposit/conversation", headers=guest_headers,
                                  json={"client_message_id": msg_id, "body": "Voici ma photo"})
    assert insured_message.status_code == 201, insured_message.text
    repeated = client.post("/v1/deposit/conversation", headers=guest_headers,
                           json={"client_message_id": msg_id, "body": "Voici ma photo"})
    assert repeated.json()["id"] == insured_message.json()["id"]
    assert client.post("/v1/deposit/conversation", headers=guest_headers,
                       json={"client_message_id": msg_id, "body": "Autre texte"}).status_code == 409
    manager_text = client.post(f"/v1/cases/{case_id}/sms-links/{message['id']}/conversation",
                               json={"client_message_id": str(uuid4()), "body": "Bien reçu"})
    assert manager_text.status_code == 201
    guest_texts = client.get("/v1/deposit/conversation", headers=guest_headers).json()
    assert [item["sender"] for item in guest_texts] == ["insured", "manager"]
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(outsider))
    assert client.get(f"/v1/cases/{case_id}/sms-links/{message['id']}/conversation").status_code == 404

    monkeypatch.setenv("SMS_LINK_DELIVERY_MODE", "twilio")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "2" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("TWILIO_SMS_MESSAGING_SERVICE_SID", "MG77be4e6c8d35a9423917e9ad99ff2c9c")
    monkeypatch.setenv("TWILIO_SMS_WEBHOOK_BASE_URL", "https://claimroom-demo-api.vercel.app/v1/webhooks/twilio/sms")
    config = TwilioSmsConfig.from_env()
    params = {"AccountSid": config.account_sid, "MessageSid": twilio_sid, "MessagingServiceSid": config.messaging_service_sid,
              "MessageStatus": "delivered", "To": NUMBER}
    callback = config.callback_url(message["id"])
    signature = RequestValidator(config.auth_token).compute_signature(callback, params)
    monkeypatch.setattr("claim_api.routes.sms_link.get_sms_link_service", lambda: manager)
    assert client.post(callback, data=params, headers={"X-Twilio-Signature": "bad"}).status_code == 403
    assert client.post(callback, data=params, headers={"X-Twilio-Signature": signature}).status_code == 204
    assert manager.list_links(actor, case_id)[0].status == "delivered"


def test_gateway_uses_claimroom_messaging_service_and_status_callback():
    calls = []
    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(sid="SM" + "3" * 32)
    config = TwilioSmsConfig("AC" + "2" * 32, "secret",
                             "MG77be4e6c8d35a9423917e9ad99ff2c9c",
                             "https://claimroom-demo-api.vercel.app/v1/webhooks/twilio/sms")
    gateway = TwilioSmsGateway(config, client=SimpleNamespace(messages=Messages()))
    message_id = uuid4()
    assert gateway.send(message_id, NUMBER, "Lien") == "SM" + "3" * 32
    assert calls == [{"messaging_service_sid": config.messaging_service_sid,
                      "to": NUMBER, "body": "Lien", "status_callback": config.callback_url(message_id)}]
