from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from claim_api.voice_intake import VoiceIntakeRequestV1, process_voice_intake
from claim_api.voice_intake import VoiceSessionView, VoiceTriageV1
from claim_api.voice_repository import VoiceEventConflict, VoiceIntakeRepository
from claim_api.main import create_app
from claim_api.case_service import CaseService
from claim_api.models import IntakePatch
from claim_api.routes.voice import get_voice_repository


CALL_AT = "2026-09-25T10:33:00+02:00"


def request(*, text: str | None = None, overrides: dict[str, tuple[str, str]] | None = None,
            missing: set[str] | None = None, event_type: str = "final_turn") -> VoiceIntakeRequestV1:
    examples = {
        "narrative": ("J'étais arrêté et un autre véhicule a heurté ma voiture.", "J'étais arrêté et un autre véhicule a heurté ma voiture."),
        "location": ("rue de Lyon à Paris", "rue de Lyon à Paris"),
        "incident_time": ("just_now", "Je viens d'avoir un accident."),
        "insured_name": ("Marie Martin", "Je suis Marie Martin."),
        "third_party_involved": ("yes", "un autre véhicule a heurté ma voiture"),
        "third_party_presence": ("left", "Le conducteur est parti."),
        "danger_status": ("no", "Je suis en sécurité."),
        "injury_severity": ("none", "Pas de blessé."),
        "stationary": ("yes", "J'étais arrêté."),
    }
    examples.update(overrides or {})
    for field in missing or set():
        examples.pop(field, None)
    if text is None:
        text = " ".join(excerpt for _, excerpt in examples.values())
    facts = [{"field": name, "value": value, "segment_id": "caller-1",
              "excerpt": excerpt, "uncertainty": "explicit"}
             for name, (value, excerpt) in examples.items()]
    return VoiceIntakeRequestV1.model_validate({
        "schema_version": 1, "provider_call_id": "vapi-call-1", "sequence": 1,
        "source_event_key": "turn-1", "event_type": event_type,
        "call_started_at": CALL_AT, "received_at": CALL_AT,
        "assistant_id": "assistant-test", "assistant_version": "assistant-v1", "extractor_version": "extractor-v1",
        "segments": [{"id": "caller-1", "speaker": "caller", "start_ms": 0,
                      "end_ms": 10000, "text": text}], "facts": facts,
    })


def test_complete_call_preserves_stationary_statement_without_redundant_question() -> None:
    intake, triage = process_voice_intake(request())
    assert triage.status == "complete"
    assert triage.next_question is None
    assert intake.narrative.startswith("J'étais arrêté")
    assert intake.time_source == "inferred_from_call"
    assert intake.incident_at == datetime(2026, 9, 25, 8, 33, tzinfo=timezone.utc)


@pytest.mark.parametrize("event_type", ["final_turn", "end_of_call_report"])
def test_only_three_p0_fields_complete_the_call(event_type: str) -> None:
    call = request(missing={"incident_time", "third_party_involved", "third_party_presence",
                            "danger_status", "injury_severity"}, event_type=event_type)
    intake, triage = process_voice_intake(call)
    assert triage.status == "complete"
    assert triage.missing_p0 == []
    assert triage.next_question is None
    assert intake.danger_status is None
    assert intake.injury_status is None
    assert intake.incident_at is None
    assert intake.insured_name == "Marie Martin"


def test_vague_time_is_not_required_and_full_name_is_asked() -> None:
    call = request(overrides={"incident_time": ("2026-09-25T09:00:00+02:00", "Ce matin.")})
    with pytest.raises(ValueError, match="explicit date and clock time"):
        process_voice_intake(call)
    intake, triage = process_voice_intake(request(missing={"incident_time"}))
    assert intake.incident_at is None
    assert triage.status == "complete"
    assert triage.missing_p0 == []
    _, needs_name = process_voice_intake(request(missing={"insured_name"}))
    assert needs_name.missing_p0 == ["insured_name"]
    assert needs_name.next_question == "Quels sont votre prénom et votre nom ?"
    _, partial_name = process_voice_intake(request(overrides={"insured_name": ("Marie", "Je suis Marie.")}))
    assert partial_name.missing_p0 == ["insured_name"]


def test_missing_location_asks_location_not_optional_plate() -> None:
    _, triage = process_voice_intake(request(missing={"location"}))
    assert triage.status == "collecting"
    assert triage.missing_p0 == ["location"]
    assert "Où l'accident" in (triage.next_question or "")


def test_urgent_handoff_supersedes_questions() -> None:
    call = request(overrides={
        "danger_status": ("yes", "Je suis en danger immédiat."),
        "injury_severity": ("serious", "Une personne est gravement blessée."),
    }, missing={"location"})
    intake, triage = process_voice_intake(call)
    assert triage.status == "urgent_human_handoff"
    assert triage.next_question is None
    assert triage.reason_codes == ["reported_immediate_danger", "reported_serious_injury"]
    assert intake.injury_status == "yes"


def test_not_safe_is_immediate_danger() -> None:
    _, triage = process_voice_intake(request(overrides={
        "danger_status": ("yes", "Je ne suis pas en sécurité."),
    }))
    assert triage.status == "urgent_human_handoff"


def test_optional_unknowns_do_not_block_call_or_become_false_negatives() -> None:
    call = request(overrides={
        "third_party_presence": ("unknown", "Je ne sais pas où est le conducteur."),
        "injury_severity": ("unknown", "Je ne sais pas s'il y a des blessés."),
    })
    intake, triage = process_voice_intake(call)
    assert triage.status == "complete"
    assert triage.next_question is None
    assert triage.missing_p0 == []
    assert intake.injury_status == "unknown"
    assert "injury_status" in intake.missing_fields


def test_unknown_third_party_does_not_force_presence_question() -> None:
    _, triage = process_voice_intake(request(overrides={
        "third_party_involved": ("unknown", "Je ne sais pas si un autre véhicule est impliqué."),
    }, missing={"third_party_presence"}))
    assert triage.status == "complete"
    assert triage.missing_p0 == []
    assert triage.next_question is None


@pytest.mark.parametrize("field,value,excerpt", [
    ("danger_status", "yes", "Pas de danger."),
    ("danger_status", "no", "Je suis en danger immédiat."),
    ("injury_severity", "serious", "Pas de blessé."),
    ("injury_severity", "none", "Une personne est gravement blessée."),
    ("third_party_involved", "yes", "Pas d'autre véhicule."),
    ("third_party_presence", "present", "Le conducteur est parti."),
])
def test_contradictory_critical_status_is_rejected(field: str, value: str, excerpt: str) -> None:
    with pytest.raises(ValueError, match="not supported"):
        process_voice_intake(request(overrides={field: (value, excerpt)}))


def test_unsourced_or_assistant_fact_is_rejected() -> None:
    payload = request().model_dump(mode="json")
    payload["facts"][0]["excerpt"] = "Invented text"
    with pytest.raises(ValueError, match="exact excerpt"):
        VoiceIntakeRequestV1.model_validate(payload)
    payload = request().model_dump(mode="json")
    payload["segments"][0]["speaker"] = "assistant"
    with pytest.raises(ValueError, match="caller segment"):
        VoiceIntakeRequestV1.model_validate(payload)


def test_end_report_without_transcript_is_incomplete() -> None:
    payload = request().model_dump(mode="json")
    payload.update({"event_type": "end_of_call_report", "segments": [], "facts": []})
    intake, triage = process_voice_intake(VoiceIntakeRequestV1.model_validate(payload))
    assert intake.incident_at is None
    assert triage.status == "incomplete"
    assert triage.next_question is None


def test_live_webhook_is_disabled_without_explicit_configuration(monkeypatch) -> None:
    monkeypatch.delenv("VOICE_LIVE_ENABLED", raising=False)
    response = TestClient(create_app()).post(
        "/v1/voice/vapi/normalized-events", json=request().model_dump(mode="json"),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_live_disabled"


def test_live_webhook_rejects_bad_credential_before_storage(monkeypatch) -> None:
    monkeypatch.setenv("VOICE_LIVE_ENABLED", "true")
    monkeypatch.setenv("VOICE_WEBHOOK_SECRET", "test-only-secret")
    monkeypatch.setenv("VOICE_OWNER_USER_ID", "00000000-0000-4000-8000-000000000843")
    response = TestClient(create_app()).post(
        "/v1/voice/vapi/normalized-events", json={**request().model_dump(mode="json"), "mode": "live"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_voice_credential"


def test_native_vapi_tool_uses_signed_call_context_not_model_arguments(monkeypatch) -> None:
    monkeypatch.setenv("VOICE_LIVE_ENABLED", "true")
    monkeypatch.setenv("VOICE_WEBHOOK_SECRET", "test-business-secret")
    monkeypatch.setenv("VOICE_OWNER_USER_ID", "00000000-0000-4000-8000-000000000843")
    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")

    class FakeRepository:
        observed_call_id = None

        def get_session(self, actor_id, call_id):
            self.observed_call_id = call_id
            return VoiceSessionView(
                case_id=UUID("00000000-0000-4000-8000-000000000844"),
                provider="vapi", provider_call_id=call_id, sequence=1,
                mode="live", triage=VoiceTriageV1(status="collecting",
                    missing_p0=["location"], next_question="Où l'accident s'est-il produit ?",
                    reason_codes=[]),
                facts=[], call_started_at=datetime.now(timezone.utc),
                assistant_id="assistant-test", assistant_version="assistant-v1",
                extractor_version="extractor-v1",
            )

    repository = FakeRepository()
    application = create_app()
    application.dependency_overrides[get_voice_repository] = lambda: repository
    response = TestClient(application).post(
        "/v1/voice/vapi/next-intake-step",
        headers={"X-Vapi-Secret": "test-business-secret"},
        json={"message": {"type": "tool-calls",
            "call": {"id": "trusted-call", "assistantId": "assistant-test"},
            "toolCallList": [{"id": "tool-1", "name": "next_intake_step",
                              "parameters": {"provider_call_id": "forged-call"}}]}},
    )
    assert response.status_code == 200
    assert repository.observed_call_id == "trusted-call"
    result = response.json()["results"][0]
    assert result["toolCallId"] == "tool-1"
    assert json.loads(result["result"])["decision"] == "ask"


def test_voice_session_is_idempotent_and_revises_one_case(monkeypatch) -> None:
    database_url = os.getenv("TEST_MIGRATION_DATABASE_URL")
    if not database_url:
        pytest.skip("requires a disposable loopback PostgreSQL test database")
    parsed = make_url(database_url)
    if parsed.host not in {"localhost", "127.0.0.1", "::1"} or "test" not in (parsed.database or "").lower():
        pytest.fail("Voice integration tests require a loopback test database")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_ALLOW_INSECURE_LOCAL", "true")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", database_url)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    actor_id = UUID("00000000-0000-4000-8000-000000000843")
    with engine.begin() as connection:
        connection.execute(text("create schema if not exists auth"))
        connection.execute(text("create table if not exists auth.users (id uuid primary key)"))
    api_root = Path(__file__).parents[1]
    command.upgrade(Config(str(api_root / "alembic.ini")), "head")
    with engine.begin() as connection:
        connection.execute(text("insert into auth.users(id) values (:actor) on conflict do nothing"), {"actor": str(actor_id)})

    repository = VoiceIntakeRepository(database_url)
    original = request().model_copy(update={"provider_call_id": f"vapi-call-{uuid4()}"})
    first = repository.ingest(actor_id, original)
    replay = repository.ingest(actor_id, original)
    assert first.session.case_id == replay.session.case_id
    assert replay.replayed
    assert first.session.triage.status == "complete"

    payload = original.model_dump(mode="json")
    payload.update({"sequence": 2, "source_event_key": "turn-2"})
    payload["facts"] = [fact for fact in payload["facts"] if fact["field"] != "location"]
    second = repository.ingest(actor_id, VoiceIntakeRequestV1.model_validate(payload))
    assert second.session.case_id == first.session.case_id
    assert second.session.triage.status == "collecting"
    assert second.session.triage.next_question and "Où l'accident" in second.session.triage.next_question
    case = repository.get_case(actor_id, first.session.case_id)
    assert case is not None
    assert case.state_version == 2 and case.content_revision == 2
    assert case.voice_session is not None and case.voice_session.status == "collecting"

    late = repository.ingest(actor_id, VoiceIntakeRequestV1.model_validate({
        **payload, "sequence": 1, "source_event_key": "late-unknown",
    }))
    assert late.replayed and late.session.sequence == 2

    end_payload = {**payload, "sequence": 3, "source_event_key": "end-1",
                   "event_type": "end_of_call_report", "segments": [], "facts": []}
    ended = repository.ingest(actor_id, VoiceIntakeRequestV1.model_validate(end_payload))
    assert ended.session.triage.status == "incomplete"
    assert ended.session.facts
    assert repository.get_case(actor_id, first.session.case_id).state_version == 3

    with pytest.raises(VoiceEventConflict):
        repository.ingest(actor_id, VoiceIntakeRequestV1.model_validate({
            **original.model_dump(mode="json"), "assistant_version": "changed",
        }))
    CaseService(repository).update_intake(str(actor_id), first.session.case_id, 3,
                                          IntakePatch(location="Lieu corrigé par le gestionnaire"))
    with pytest.raises(VoiceEventConflict, match="Handler-edited"):
        repository.ingest(actor_id, VoiceIntakeRequestV1.model_validate({
            **original.model_dump(mode="json"), "sequence": 4,
            "source_event_key": "late-correction",
        }))
    with engine.connect() as connection:
        assert connection.execute(text(
            "select count(*) from public.voice_sessions where provider_call_id = :call_id"
        ), {"call_id": original.provider_call_id}).scalar_one() == 1
        assert connection.execute(text(
            "select count(*) from public.audit_events where case_id = :case and event_type = 'case.voice_intake_recorded'"
        ), {"case": str(first.session.case_id)}).scalar_one() == 3
    engine.dispose()
