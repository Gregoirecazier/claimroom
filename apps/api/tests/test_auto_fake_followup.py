"""Automatic fake delivery is tied to the finished phone call boundary."""

from types import SimpleNamespace
from uuid import uuid4

from fastapi import BackgroundTasks
from fastapi import HTTPException
import pytest

from claim_api import vapi_native
from claim_api.routes import sms, voice
from test_voice_intake import request as voice_request


def test_non_fake_configuration_does_not_expose_auto_follow_up(monkeypatch):
    monkeypatch.setenv("WHATSAPP_DELIVERY_MODE", "twilio_production")
    with pytest.raises(HTTPException) as error:
        sms.get_fake_message_service()
    assert error.value.status_code == 404
    assert error.value.detail["code"] == "fake_whatsapp_not_configured"


def test_real_sms_mode_suppresses_fake_follow_up(monkeypatch):
    monkeypatch.setenv("WHATSAPP_DELIVERY_MODE", "fake_whatsapp")
    monkeypatch.setenv("SMS_LINK_DELIVERY_MODE", "twilio")
    with pytest.raises(HTTPException) as error:
        sms.get_fake_message_service()
    assert error.value.status_code == 404
    monkeypatch.setattr(sms, "get_message_service", lambda: pytest.fail("fake send must not run"))
    voice._auto_fake_follow_up(uuid4(), uuid4())


def test_final_phone_report_dispatches_fake_once_and_web_report_does_not(monkeypatch):
    actor_id, case_id = uuid4(), uuid4()
    monkeypatch.setenv("WHATSAPP_DELIVERY_MODE", "fake_whatsapp")
    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")
    monkeypatch.setattr(voice, "_ingest", lambda _repo, _actor, _request:
                        SimpleNamespace(session=SimpleNamespace(case_id=case_id)))
    calls = []
    monkeypatch.setattr(sms, "get_message_service", lambda: SimpleNamespace(
        auto_fake_follow_up=lambda actor, case: calls.append((actor, case))))
    phone = voice_request(event_type="end_of_call_report").model_copy(update={
        "mode": "live", "telephony_provider": "twilio",
    })
    voice.vapi_event(phone, actor_id, object())
    assert calls == [(actor_id, case_id)]
    voice.vapi_event(phone.model_copy(update={"telephony_provider": "web"}), actor_id, object())
    voice.vapi_event(phone.model_copy(update={"event_type": "final_turn"}), actor_id, object())
    assert calls == [(actor_id, case_id)]


def test_native_end_report_schedules_fake_delivery_only_for_phone_calls(monkeypatch):
    actor_id, case_id = uuid4(), uuid4()
    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")
    monkeypatch.setattr(vapi_native, "_ingest", lambda *_args: SimpleNamespace(
        replayed=False,
        session=SimpleNamespace(case_id=case_id, telephony_provider="twilio"),
        model_dump=lambda **_kwargs: {"case_id": str(case_id)},
    ))
    repository = SimpleNamespace(event_exists=lambda *_args: False, get_state=lambda *_args: None)
    payload = {"message": {"type": "end-of-call-report", "call": {
        "id": f"call-{uuid4().hex}", "assistantId": "assistant-test",
        "startedAt": "2026-09-25T08:33:00Z",
    }, "artifact": {"messages": []}}}
    tasks = BackgroundTasks()
    vapi_native.server_event(payload, actor_id, repository, tasks)
    assert tasks.tasks[0].func is voice._auto_fake_follow_up
    assert tasks.tasks[0].args == (actor_id, case_id)

    payload["message"]["call"]["type"] = "webCall"
    monkeypatch.setattr(vapi_native, "_ingest", lambda *_args: SimpleNamespace(
        replayed=False,
        session=SimpleNamespace(case_id=case_id, telephony_provider="web"),
        model_dump=lambda **_kwargs: {"case_id": str(case_id)},
    ))
    web_tasks = BackgroundTasks()
    vapi_native.server_event(payload, actor_id, repository, web_tasks)
    assert all(task.func is not voice._auto_fake_follow_up for task in web_tasks.tasks)
