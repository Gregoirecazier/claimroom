"""The fake phone receives only its case and number, and replies become private history."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.fake_whatsapp import FAKE_PROVIDER, FakeWhatsAppGateway, FakeWhatsAppService
from claim_api.insured_portal import InsuredPortalService
from claim_api.main import create_app
from claim_api.routes.sms import get_message_service
from claim_api.sms_service import CaseMessageService, SmsError
from claim_api.voice_repository import VoiceIntakeRepository
from test_sms_service import _create, _preview, sms_db
from test_voice_intake import request as voice_request


def test_finished_phone_call_gets_one_automatic_fake_follow_up(sms_db):
    engine, base, actor, _outsider, _case_id, _other_case = sms_db
    call = voice_request(event_type="end_of_call_report").model_copy(update={
        "mode": "live", "telephony_provider": "twilio", "provider_call_id": f"call-{uuid4().hex}",
    })
    case_id = VoiceIntakeRepository(base.database_url).ingest(actor, call).session.case_id

    class CallerLookup:
        def candidate(self, call_id):
            assert call_id == call.provider_call_id
            return "+33612345678"

    manager = CaseMessageService(base.database_url, base.grants, caller_lookup=CallerLookup(),
                                 gateway=FakeWhatsAppGateway())
    first = manager.auto_fake_follow_up(actor, case_id)
    second = manager.auto_fake_follow_up(actor, case_id)
    assert first.id == second.id and first.status == "delivered"
    assert first.provider == FAKE_PROVIDER
    assert len(manager.list_messages(actor, case_id)) == 1
    token = manager.link(actor, case_id, first.id).url.split("#token=", 1)[1]
    conversation = FakeWhatsAppService(base.database_url).get(
        InsuredPortalService(base.database_url).exchange(token)["session_token"])
    assert conversation["number"] == "+33612345678"
    assert len(conversation["messages"]) == 1
    with pytest.raises(SmsError) as error:
        base.auto_fake_follow_up(actor, case_id)
    assert error.value.code == "fake_whatsapp_not_configured"


def test_fake_gateway_dispatches_into_phone_and_scopes_replies(sms_db):
    engine, base, actor, _outsider, case_id, _other_case = sms_db
    manager = CaseMessageService(base.database_url, base.grants, gateway=FakeWhatsAppGateway())
    phone = FakeWhatsAppService(base.database_url)
    number_a = "+33" + str(int(uuid4().hex[:8], 16) % 1_000_000_000).zfill(9)
    number_b = "+33" + str(int(uuid4().hex[:8], 16) % 1_000_000_000).zfill(9)
    preview_a = _preview(manager, actor, case_id, number=number_a)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(actor))
    app.dependency_overrides[get_message_service] = lambda: manager
    client = TestClient(app)
    created = client.post(f"/v1/cases/{case_id}/messages", json={
        "expected_state_version": 1, "idempotency_key": "fake-first",
        "recipient_confirmation": {"kind": "manager_correction", "number": number_a,
                                   "reason": "Numéro confirmé par le gestionnaire"},
        "deposit_grant_id": str(preview_a.deposit_grant_id), "deposit_url": preview_a.deposit_url,
        "preview_hash": preview_a.preview_hash,
    })
    assert created.status_code == 201
    delivered_a = created.json()["message"]
    assert delivered_a["mode"] == "mock" and delivered_a["status"] == "delivered"
    assert delivered_a["provider"] == FAKE_PROVIDER
    token_a = preview_a.deposit_url.split("#token=", 1)[1]
    session_a = InsuredPortalService(base.database_url).exchange(token_a)["session_token"]
    first = phone.get(session_a)
    assert first["number"] == number_a and first["deposit_url"] == preview_a.deposit_url
    assert len(first["messages"]) == 1 and first["messages"][0]["text"] == preview_a.body

    message_uuid = uuid4()
    replied = phone.reply(session_a, client_message_id=message_uuid, body="Voici ma réponse", evidence_id=None)
    assert len(replied["messages"]) == 2
    assert replied["messages"][1]["side"] == "you" and replied["messages"][1]["text"] == "Voici ma réponse"
    assert len(phone.reply(session_a, client_message_id=message_uuid, body="Voici ma réponse", evidence_id=None)["messages"]) == 2
    assert manager.list_inbound(actor, case_id)[0].body == "Voici ma réponse"
    with pytest.raises(SmsError) as error:
        phone.reply(session_a, client_message_id=message_uuid, body="Autre réponse", evidence_id=None)
    assert error.value.code == "idempotency_conflict"

    preview_b = _preview(manager, actor, case_id, version=2, number=number_b)
    created_b = _create(manager, actor, case_id, preview_b, key="second", version=2, number=number_b)
    assert created_b.message.status == "delivered"
    token_b = preview_b.deposit_url.split("#token=", 1)[1]
    session_b = InsuredPortalService(base.database_url).exchange(token_b)["session_token"]
    second = phone.get(session_b)
    assert second["number"] == number_b and len(second["messages"]) == 1
    assert all("Voici ma réponse" not in item["text"] for item in second["messages"])

    evidence_id = uuid4()
    with engine.begin() as connection:
        connection.execute(text("""insert into public.evidence
            (id, case_id, storage_path, kind, source_kind, mode, mime_type, byte_size,
             client_sha256, checksum_status)
            values (:id, :case_id, :path, 'scene_photo', 'insured_upload', 'live',
                    'image/png', 4, :digest, 'client_declared')"""),
            {"id": evidence_id, "case_id": case_id, "path": f"{case_id}/{evidence_id}.png", "digest": "a" * 64})
        connection.execute(text("""insert into public.evidence_upload_intents
            (case_id, actor_user_id, storage_path, kind, mime_type, byte_size, client_sha256,
             expires_at, finalized_evidence_id, finalized_at, source_kind, deposit_grant_id)
            values (:case_id, :actor, :path, 'scene_photo', 'image/png', 4, :digest,
                    now() + interval '1 hour', :evidence_id, now(), 'insured_upload', :grant_id)"""),
            {"case_id": case_id, "actor": actor, "path": f"{case_id}/{evidence_id}.png",
             "digest": "a" * 64, "evidence_id": evidence_id, "grant_id": preview_a.deposit_grant_id})
    assert phone.reply(session_a, client_message_id=uuid4(), body="", evidence_id=evidence_id)["messages"][-1]["evidence_id"] == str(evidence_id)
    with pytest.raises(SmsError) as error:
        phone.reply(session_b, client_message_id=uuid4(), body="", evidence_id=evidence_id)
    assert error.value.code == "evidence_not_found"
