"""S09 contract on a disposable PostgreSQL database, including S10 association."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.deposit_grants import DepositGrantService
from claim_api.main import create_app
from claim_api.routes.sms import get_message_service
from claim_api.sms_models import (RecipientConfirmation, SmsCreateRequest,
                                  SmsMockTransitionRequest, SmsPreviewRequest)
from claim_api.sms_service import CaseMessageService, CaseSnapshot, SmsError
from claim_api.models import Intake
from test_alembic_version import _apply_alembic, _test_database_url


@pytest.fixture
def sms_db(monkeypatch):
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    actor, outsider = uuid4(), uuid4()
    first, second = uuid4(), uuid4()
    intake = '{"reported_at":"2026-09-25T12:00:00Z","narrative":"Un accrochage est survenu au carrefour."}'
    with engine.begin() as connection:
        for user in (actor, outsider):
            connection.execute(text("insert into auth.users(id) values (:id)"), {"id": user})
        for case_id, owner in ((first, actor), (second, outsider)):
            connection.execute(text("""insert into public.cases
                (id, created_by_user_id, scenario_id, intake_json)
                values (:id, :owner, 'g1', cast(:intake as jsonb))"""),
                {"id": case_id, "owner": owner, "intake": intake})
    grants = DepositGrantService(database_url, "https://claim.example/depot")
    service = CaseMessageService(database_url, grants)
    yield engine, service, actor, outsider, first, second
    engine.dispose()


def _preview(service, actor, case_id, version=1, number="+33123456789"):
    return service.preview(actor, case_id, SmsPreviewRequest(
        expected_state_version=version,
        recipient_confirmation=RecipientConfirmation(
            kind="manager_correction", number=number, reason="Numéro confirmé par le gestionnaire")))


def _create(service, actor, case_id, preview, *, key="first", version=1, number="+33123456789"):
    return service.create(actor, case_id, SmsCreateRequest(
        expected_state_version=version, idempotency_key=key,
        recipient_confirmation=RecipientConfirmation(
            kind="manager_correction", number=number, reason="Numéro confirmé par le gestionnaire"),
        deposit_grant_id=preview.deposit_grant_id, deposit_url=preview.deposit_url,
        preview_hash=preview.preview_hash))


def test_incomplete_voice_can_request_follow_up_but_urgent_voice_cannot():
    base = CaseSnapshot(
        case_id=uuid4(), scenario_id="voice_web", state_version=1, content_revision=1,
        intake=Intake.model_validate({"reported_at": "2026-09-25T12:00:00Z", "narrative": "Accident."}),
        evidence_kinds=frozenset(), voice_status="incomplete", voice_facts=(), call_id="call-1",
    )
    assert CaseMessageService._check_ready(base) == ["voice_intake_incomplete"]
    with pytest.raises(SmsError) as error:
        CaseMessageService._check_ready(replace(base, voice_status="urgent_human_handoff"))
    assert error.value.code == "urgent_handoff"


def test_preview_create_replay_private_history_and_delivery_states(sms_db):
    engine, service, actor, outsider, first, _second = sms_db
    preview = _preview(service, actor, first)
    assert preview.deposit_url in preview.body
    assert preview.recipient_masked.endswith("6789")
    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from public.case_messages where case_id=:id"),
                                  {"id": first}).scalar_one() == 0
    created = _create(service, actor, first, preview)
    assert created.message.status == "queued" and created.message.mode == "mock"
    assert created.state_version == 2
    replay = _create(service, actor, first, preview)
    assert replay.replayed and replay.message.id == created.message.id
    history = service.list_messages(actor, first)
    assert len(history) == 1 and "#token=" not in history[0].body_preview
    assert service.link(actor, first, created.message.id).url == preview.deposit_url
    with pytest.raises(SmsError, match="Case not found"):
        service.list_messages(outsider, first)
    with engine.connect() as connection:
        message = connection.execute(text("select body, recipient, channel, provider, provider_message_id from public.case_messages where id=:id"),
                                     {"id": created.message.id}).one()
        assert message.body == preview.body and message.recipient == "+33123456789"
        assert message.channel == "whatsapp" and message.provider == "claimroom.mock.whatsapp"
        assert message.provider_message_id is None
    version = created.state_version
    for status in ("accepted", "sent", "delivered"):
        changed = service.mock_transition(actor, first, created.message.id,
                                          SmsMockTransitionRequest(expected_state_version=version, status=status))
        assert changed.message.status == status and not changed.replayed
        version = changed.state_version
    repeated = service.mock_transition(actor, first, created.message.id,
                                       SmsMockTransitionRequest(expected_state_version=2, status="delivered"))
    assert repeated.replayed and repeated.state_version == version
    with engine.connect() as connection:
        case = connection.execute(text("select content_revision, state_version from public.cases where id=:id"),
                                  {"id": first}).one()
        assert case.content_revision == 1 and case.state_version == version
        message = connection.execute(text("select body, recipient, sent_at, delivered_at from public.case_messages where id=:id"),
                                     {"id": created.message.id}).one()
        assert message.body == preview.body and message.recipient == "+33123456789"
        assert message.sent_at is not None and message.delivered_at is not None
        audit = connection.execute(text("""select metadata_json from public.audit_events
            where case_id=:id and event_type='case.whatsapp_created'"""), {"id": first}).scalar_one()
        assert audit["recipient_correction_reason"] == "Numéro confirmé par le gestionnaire"
        assert "+33123456789" not in str(audit) and "#token=" not in str(audit)


def test_changed_case_conflicting_key_and_cross_case_grant_fail(sms_db):
    engine, service, actor, outsider, first, second = sms_db
    preview = _preview(service, actor, first)
    with engine.begin() as connection:
        connection.execute(text("""update public.cases
            set content_revision=2, state_version=2 where id=:id"""), {"id": first})
    with pytest.raises(SmsError) as error:
        _create(service, actor, first, preview)
    assert error.value.code == "stale_case"
    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from public.case_messages where case_id=:id"),
                                  {"id": first}).scalar_one() == 0
    fresh = _preview(service, actor, first, version=2)
    created = _create(service, actor, first, fresh, version=2)
    with pytest.raises(SmsError) as error:
        _create(service, actor, first, fresh, version=2, number="+33987654321")
    assert error.value.code == "idempotency_conflict"
    other_preview = _preview(service, outsider, second)
    with pytest.raises(SmsError) as error:
        _create(service, actor, first, other_preview, key="cross", version=3)
    assert error.value.code in {"stale_preview", "invalid_grant"}
    with pytest.raises(SmsError) as error:
        service.mock_transition(actor, first, created.message.id,
                                SmsMockTransitionRequest(expected_state_version=3, status="delivered"))
    assert error.value.code == "invalid_transition"


def test_manager_routes_return_private_preview_and_masked_history(sms_db):
    _engine, service, actor, _outsider, first, _second = sms_db
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(actor))
    app.dependency_overrides[get_message_service] = lambda: service
    client = TestClient(app)
    confirmation = {"kind": "manager_correction", "number": "+33123456789",
                    "reason": "Numéro confirmé par le gestionnaire"}
    preview = client.post(f"/v1/cases/{first}/messages/preview",
                          json={"expected_state_version": 1, "recipient_confirmation": confirmation})
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    assert preview.headers["referrer-policy"] == "no-referrer"
    draft = preview.json()
    assert draft["deposit_url"] in draft["body"]
    created = client.post(f"/v1/cases/{first}/messages", json={
        "expected_state_version": 1, "idempotency_key": "route-first",
        "recipient_confirmation": confirmation, "deposit_grant_id": draft["deposit_grant_id"],
        "deposit_url": draft["deposit_url"], "preview_hash": draft["preview_hash"],
    })
    assert created.status_code == 201 and created.headers["cache-control"] == "no-store"
    message_id = created.json()["message"]["id"]
    history = client.get(f"/v1/cases/{first}/messages")
    assert history.status_code == 200 and history.headers["cache-control"] == "no-store"
    assert "#token=" not in history.text
    link = client.get(f"/v1/cases/{first}/messages/{message_id}/link")
    assert link.status_code == 200 and link.json()["url"] == draft["deposit_url"]
    assert link.headers["cache-control"] == "no-store"
