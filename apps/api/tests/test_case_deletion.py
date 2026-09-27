from uuid import uuid4

import psycopg
import pytest

from claim_api.case_service import CaseNotFoundError, CaseService
from claim_api.postgres_cases import PostgresCaseRepository
from test_alembic_version import _apply_alembic, _test_database_url


def test_postgres_deletes_populated_case_without_touching_other_cases(monkeypatch):
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    actor = uuid4()
    with psycopg.connect(database_url) as connection:
        connection.execute("insert into auth.users (id) values (%s)", (actor,))
    service = CaseService(PostgresCaseRepository(database_url))
    case = service.create_case(str(actor), "g1")
    keep = service.create_case(str(actor), "g1")
    ids = {name: uuid4() for name in (
        "evidence", "draft1", "draft2", "approval", "registration", "message", "grant", "session", "voice", "analysis", "dust",
    )}
    ids.update(case=case.id, actor=actor, hash="a" * 64)
    with psycopg.connect(database_url) as connection:
        for statement in (
            """insert into public.evidence
               (id, case_id, storage_path, kind, source_kind, mode, mime_type, byte_size, checksum_status)
               values (%(evidence)s, %(case)s, %(case)s || '/quote.pdf', 'document', 'handler_upload',
                       'mock', 'application/pdf', 100, 'client_declared')""",
            """insert into public.case_quotes
               (case_id, version, evidence_id, total_ttc_minor, amount_source, attached_estimate_version, created_by_user_id)
               values (%(case)s, 1, %(evidence)s, 100, 'handler_entered', 1, %(actor)s)""",
            """insert into public.drafts (id, case_id, version, content_revision, body, sha256)
               values (%(draft1)s, %(case)s, 1, 1, 'First draft', %(hash)s)""",
            """insert into public.drafts (id, case_id, parent_draft_id, version, content_revision, body, sha256)
               values (%(draft2)s, %(case)s, %(draft1)s, 2, 1, 'Second draft', %(hash)s)""",
            "update public.cases set current_draft_id=%(draft2)s, status='sent' where id=%(case)s",
            """insert into public.approvals
               (id, case_id, draft_id, actor_user_id, draft_sha256, approved_content_revision)
               values (%(approval)s, %(case)s, %(draft2)s, %(actor)s, %(hash)s, 1)""",
            """insert into public.actions
               (id, case_id, approval_id, draft_id, kind, status, idempotency_key, payload_hash)
               values (%(registration)s, %(case)s, %(approval)s, %(draft2)s, 'registration', 'confirmed', 'reg', %(hash)s)""",
            """insert into public.actions
               (case_id, approval_id, draft_id, registration_action_id, kind, status, idempotency_key, payload_hash)
               values (%(case)s, %(approval)s, %(draft2)s, %(registration)s, 'send', 'confirmed', 'send', %(hash)s)""",
            """insert into public.case_messages
               (id, case_id, recipient, body, provider, idempotency_key, payload_hash)
               values (%(message)s, %(case)s, '+33600000000', 'Test', 'mock', 'message', %(hash)s)""",
            """insert into public.case_whatsapp_inbound
               (case_id, provider_message_id, sender, body, evidence_id)
               values (%(case)s, %(case)s, '+33600000000', 'Test attachment', %(evidence)s)""",
            """insert into public.deposit_grants
               (id, case_id, token_sha256, capabilities, content_revision, expires_at, message_id)
               values (%(grant)s, %(case)s, %(hash)s, array['read_summary'], 1, now() + interval '1 hour', %(message)s)""",
            """insert into public.deposit_sessions (id, grant_id, token_sha256, expires_at)
               values (%(session)s, %(grant)s, %(hash)s, now() + interval '1 hour')""",
            """insert into public.insured_corrections
               (case_id, grant_id, session_id, field_name, previous_value_json, new_value_json, content_revision)
               values (%(case)s, %(grant)s, %(session)s, 'location', '"Paris"', '"Lille"', 1)""",
            """insert into public.deposit_demo_media (case_id, media_key, evidence_id, attached_by_grant_id)
               values (%(case)s, 'test', %(evidence)s, %(grant)s)""",
            """insert into public.case_portal_messages (case_id, grant_id, sender, client_message_id, body)
               values (%(case)s, %(grant)s, 'insured', %(message)s, 'Test portal message')""",
            """insert into public.deposit_chat_history (case_id, revision, state_json)
               values (%(case)s, 1, '{}')""",
            """insert into public.garage_sms_settings (case_id, recipient)
               values (%(case)s, '+33600000000')""",
            """insert into public.garage_reply_jobs (case_id, trigger_id, mode, recipient, message_id)
               values (%(case)s, %(case)s, 'mock', '+33600000000', %(message)s)""",
            """insert into public.analysis_runs (id, case_id, input_content_revision, method_version, status, input_json)
               values (%(analysis)s, %(case)s, 1, 'test', 'ready', '{}')""",
            """insert into public.dust_runs (id, case_id, agent, agent_id, workspace_id, base_url,
               input_content_revision, idempotency_key, request_hash, gemini_analysis_run_id, status, input_json)
               values (%(dust)s, %(case)s, 'garage', 'test', 'test', 'https://dust.tt', 1,
                       %(dust)s, %(hash)s, %(analysis)s, 'ready', '{}')""",
            """insert into public.case_media_workflows (case_id, content_revision, analysis_run_id, garage_run_id)
               values (%(case)s, 1, %(analysis)s, %(dust)s)""",
            """insert into public.case_correspondence (case_id, kind, content_revision, source_run_id, subject, body)
               values (%(case)s, 'garage', 1, %(dust)s, 'Test subject', 'Test body')""",
            """insert into public.voice_sessions
               (id, case_id, provider, provider_call_id, mode, telephony_provider, speech_provider,
                call_started_at, assistant_id, assistant_version, extractor_version, sequence,
                triage_json, projected_intake_json, transcript_json, facts_json)
               values (%(voice)s, %(case)s, 'vapi', %(case)s, 'mock', 'web', 'gradium', now(),
                       'test', '1', '1', 1, '{}', '{}', '[]', '[]')""",
            """insert into public.voice_session_events
               (session_id, source_event_key, sequence, event_type, payload_sha256,
                transcript_json, facts_json, triage_json, received_at)
               values (%(voice)s, 'test', 1, 'end_of_call_report', %(hash)s, '[]', '[]', '{}', now())""",
        ):
            connection.execute(statement, ids)

    with pytest.raises(CaseNotFoundError):
        service.delete_case(str(uuid4()), case.id)
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select count(*) from public.cases where id=%s", (case.id,)).fetchone()[0] == 1
    service.delete_case(str(actor), case.id)
    assert [item.id for item in service.list_cases(str(actor), 50)] == [keep.id]
    assert service.get_case(str(actor), keep.id).estimate is not None
    with pytest.raises(CaseNotFoundError):
        service.get_case(str(actor), case.id)
    with pytest.raises(CaseNotFoundError):
        service.delete_case(str(actor), case.id)
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select count(*) from public.deposit_sessions where id=%s", (ids['session'],)).fetchone()[0] == 0
        assert connection.execute("select count(*) from public.voice_session_events where session_id=%s", (ids['voice'],)).fetchone()[0] == 0
