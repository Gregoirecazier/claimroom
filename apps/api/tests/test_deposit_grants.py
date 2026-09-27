from __future__ import annotations

from datetime import timedelta
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.deposit_grants import DepositGrantError, DepositGrantService
from claim_api.main import create_app
from claim_api.routes.deposit_grants import get_deposit_grant_service
from test_alembic_version import _apply_alembic, _test_database_url


def test_deposit_grant_migration_renders_without_database(monkeypatch):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://unused:unused@localhost/claim_test?sslmode=require")
    output = StringIO()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "20260925_counterparty_lookup:20260925_deposit_grants", sql=True)
    rendered = output.getvalue().lower()
    assert "create table public.deposit_grants" in rendered
    assert "token_sha256" in rendered
    assert "enable row level security" in rendered


@pytest.fixture
def grant_db(monkeypatch):
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    actor, other_actor = uuid4(), uuid4()
    first, second = uuid4(), uuid4()
    with engine.begin() as connection:
        for user in (actor, other_actor):
            connection.execute(text("insert into auth.users(id) values (:id) on conflict do nothing"), {"id": user})
        for case, owner in ((first, actor), (second, other_actor)):
            connection.execute(text("""insert into public.cases
                (id, created_by_user_id, scenario_id, intake_json)
                values (:case, :actor, 'grant-test', '{}'::jsonb)"""), {"case": case, "actor": owner})
    yield database_url, engine, actor, first, second
    engine.dispose()


def _message(cursor, case_id):
    cursor.execute("""insert into public.case_messages
        (case_id, recipient, body, provider, idempotency_key, payload_hash)
        values (%s, '+33100000000', 'Synthetic follow-up', 'mock', %s, %s)
        returning id""", (case_id, str(uuid4()), "a" * 64))
    return cursor.fetchone()["id"]


def test_issue_associate_validate_revoke_and_case_isolation(grant_db):
    database_url, engine, actor, first, second = grant_db
    service = DepositGrantService(database_url, "https://claim.example/depot")
    issued = service.issue(first, 1, timedelta(hours=1), actor_id=actor, expected_state_version=1)
    token = issued.url.split("#token=", 1)[1]
    assert issued.url.startswith("https://claim.example/depot#token=")
    with engine.connect() as connection:
        row = connection.execute(text("select token_sha256, message_id from public.deposit_grants where id=:id"),
                                 {"id": issued.grant_id}).one()
        assert row.token_sha256 != token and len(row.token_sha256) == 64
        assert row.message_id is None
    with pytest.raises(DepositGrantError, match="invalid_grant"):
        service.validate(token, first)  # Preview is not a live deposit link yet.
    with service._connect() as connection, connection.cursor() as cursor:
        service.validate_url(cursor, issued.grant_id, first, issued.url, 1)
        message_id = _message(cursor, first)
        service.associate(cursor, issued.grant_id, message_id, first)
    assert service.validate(token, first).message_id == message_id
    with pytest.raises(DepositGrantError, match="grant_already_associated"):
        with service._connect() as connection, connection.cursor() as cursor:
            service.validate_url(cursor, issued.grant_id, first, issued.url, 1)
    with engine.begin() as connection:
        connection.execute(text("update public.cases set content_revision=2 where id=:id"), {"id": first})
    assert service.validate(token, first, "upload_evidence").message_id == message_id
    with pytest.raises(DepositGrantError, match="invalid_grant"):
        service.validate(token, second)
    service.revoke(issued.grant_id, first, actor_id=actor, expected_state_version=1)
    with pytest.raises(DepositGrantError, match="revoked_grant"):
        service.validate(token, first)


def test_mismatched_message_tampered_link_expiry_and_revision_fail(grant_db):
    database_url, engine, actor, first, second = grant_db
    service = DepositGrantService(database_url, "https://claim.example/depot")
    issued = service.issue(first, 1, actor_id=actor)
    with service._connect() as connection, connection.cursor() as cursor:
        with pytest.raises(DepositGrantError, match="invalid_grant"):
            service.validate_url(cursor, issued.grant_id, first, issued.url + "x", 1)
        service.validate_url(cursor, issued.grant_id, first, issued.url, 1)
        other_message = _message(cursor, second)
        with pytest.raises(DepositGrantError, match="invalid_grant"):
            service.associate(cursor, issued.grant_id, other_message, first)
    with engine.begin() as connection:
        connection.execute(text("update public.cases set content_revision=2 where id=:id"), {"id": first})
    with pytest.raises(DepositGrantError, match="stale_case"):
        with service._connect() as connection, connection.cursor() as cursor:
            service.validate_url(cursor, issued.grant_id, first, issued.url, 1)
    second_grant = service.issue(second, 1, actor_id=None)
    with engine.begin() as connection:
        connection.execute(text("""update public.deposit_grants
            set created_at=now()-interval '2 days', expires_at=now()-interval '1 second'
            where id=:id"""), {"id": second_grant.grant_id})
    with pytest.raises(DepositGrantError, match="expired_grant"):
        with service._connect() as connection, connection.cursor() as cursor:
            service.validate_url(cursor, second_grant.grant_id, second, second_grant.url, 1)
    with pytest.raises(DepositGrantError, match="not_found"):
        service.issue(second, 1, actor_id=actor)


def test_invalid_ttl_and_capabilities_are_rejected_before_insert(grant_db):
    database_url, _engine, actor, first, _second = grant_db
    service = DepositGrantService(database_url, "https://claim.example/depot")
    with pytest.raises(DepositGrantError, match="invalid_ttl"):
        service.issue(first, 1, timedelta(0), actor_id=actor)
    with pytest.raises(DepositGrantError, match="forbidden_capability"):
        service.issue(first, 1, actor_id=actor, capabilities=("approve",))


def test_manager_routes_require_case_owner_and_do_not_cache_link(grant_db):
    database_url, _engine, actor, first, second = grant_db
    service = DepositGrantService(database_url, "https://claim.example/depot")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(actor))
    app.dependency_overrides[get_deposit_grant_service] = lambda: service
    client = TestClient(app)
    request = {"expected_state_version": 1, "content_revision": 1}
    denied = client.post(f"/v1/cases/{second}/deposit-grants", json=request)
    assert denied.status_code == 404
    issued = client.post(f"/v1/cases/{first}/deposit-grants", json=request)
    assert issued.status_code == 201
    assert issued.headers["cache-control"] == "no-store"
    assert issued.headers["referrer-policy"] == "no-referrer"
    grant = issued.json()
    assert grant["url"].startswith("https://claim.example/depot#token=")
    revoked = client.post(f"/v1/cases/{first}/deposit-grants/{grant['grant_id']}/revoke",
                          json={"expected_state_version": 1})
    assert revoked.status_code == 204
