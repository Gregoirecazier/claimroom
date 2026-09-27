from __future__ import annotations

from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from test_alembic_version import _apply_alembic, _test_database_url


def test_message_migration_can_render_upgrade_and_downgrade_without_database(monkeypatch):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://unused:unused@localhost/claim_test?sslmode=require")
    output = StringIO()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "20260925_evidence:20260925_case_messages", sql=True)
    assert "CREATE TABLE" in output.getvalue().upper()
    assert "case_messages" in output.getvalue()
    output.truncate(0)
    output.seek(0)
    command.downgrade(config, "20260925_case_messages:20260925_evidence", sql=True)
    assert "drop table public.case_messages" in output.getvalue().lower()


@pytest.fixture
def message_db(monkeypatch):
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as connection:
        transaction = connection.begin()
        actor, case = str(uuid4()), str(uuid4())
        connection.execute(text("insert into auth.users(id) values (:id)"), {"id": actor})
        connection.execute(text("""insert into public.cases
            (id, created_by_user_id, scenario_id, intake_json)
            values (:case, :actor, 'sms-test', '{}'::jsonb)"""), {"case": case, "actor": actor})
        yield connection, case
        transaction.rollback()
    engine.dispose()


def insert_message(connection, case, **changes):
    values = dict(case=case, recipient="+33100000000", body="Synthetic follow-up",
                  status="queued", mode="mock", provider="demo", key=str(uuid4()),
                  hash="a" * 64, provider_id=None, sent=None, delivered=None)
    values.update(changes)
    return connection.execute(text("""insert into public.case_messages
        (case_id, recipient, body, status, mode, provider, idempotency_key,
         payload_hash, provider_message_id, sent_at, delivered_at)
        values (:case, :recipient, :body, :status, :mode, :provider, :key,
                :hash, :provider_id, :sent, :delivered) returning id"""), values).scalar_one()


def test_message_before_approval_and_delivery_preserve_case_revision(message_db):
    connection, case = message_db
    message = insert_message(connection, case)
    connection.execute(text("""update public.case_messages set status='delivered',
        sent_at=now(), delivered_at=now(), updated_at=now(), provider_message_id='demo-1'
        where id=:id"""), {"id": message})
    assert connection.execute(text("select content_revision from public.cases where id=:id"),
                              {"id": case}).scalar_one() == 1
    assert connection.execute(text("select count(*) from public.approvals where case_id=:id"),
                              {"id": case}).scalar_one() == 0
    assert connection.execute(text("select relrowsecurity from pg_class where oid='public.case_messages'::regclass")).scalar_one()
    assert connection.execute(text("select count(*) from pg_policies where schemaname='public' and tablename='case_messages'")).scalar_one() == 0
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("delete from public.cases where id=:id"), {"id": case})


@pytest.mark.parametrize("change", [
    {"recipient": "not-a-phone"}, {"body": " "}, {"mode": "synthetic"},
    {"status": "invalid"}, {"status": "delivered"}, {"status": "sent"},
    {"hash": "invalid"}, {"key": " "}, {"case": str(uuid4())},
    {"sent": "2026-09-25T12:00:00Z", "delivered": "2026-09-25T11:00:00Z"},
])
def test_invalid_message_is_rejected(message_db, change):
    connection, case = message_db
    values = {"case": case, **change}
    with pytest.raises(IntegrityError), connection.begin_nested():
        insert_message(connection, **values)


def test_duplicate_intent_or_provider_receipt_is_rejected(message_db):
    connection, case = message_db
    insert_message(connection, case, key="one-intent", provider_id="one-receipt")
    for changes in ({"key": "one-intent"}, {"provider_id": "one-receipt"}):
        with pytest.raises(IntegrityError), connection.begin_nested():
            insert_message(connection, case, **changes)
