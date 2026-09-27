from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from claim_api.main import create_app
from claim_api.routes.voice import get_voice_repository
from claim_api.voice_repository import VoiceIntakeRepository


STARTED = "2026-09-25T10:33:00+02:00"
STORY = ("Je viens d'avoir un accident. J'étais arrêté et un autre véhicule a heurté ma voiture. "
         "Le conducteur est parti. Je suis en sécurité. Pas de blessé. rue de Lyon à Paris")


def _configure(monkeypatch, database_url: str) -> TestClient:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_ALLOW_INSECURE_LOCAL", "true")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", database_url)
    monkeypatch.setenv("VOICE_LIVE_ENABLED", "true")
    monkeypatch.setenv("VOICE_WEBHOOK_SECRET", "test-business-secret")
    monkeypatch.setenv("VOICE_OWNER_USER_ID", "00000000-0000-4000-8000-000000000843")
    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text("create schema if not exists auth"))
        connection.execute(text("create table if not exists auth.users (id uuid primary key)"))
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
    with engine.begin() as connection:
        connection.execute(text("insert into auth.users(id) values (:id) on conflict do nothing"),
                           {"id": "00000000-0000-4000-8000-000000000843"})
    engine.dispose()
    app = create_app()
    app.dependency_overrides[get_voice_repository] = lambda: VoiceIntakeRepository(database_url)
    return TestClient(app)


def _db_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL")
    if not url:
        pytest.skip("requires a disposable loopback PostgreSQL test database")
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1", "::1"} or "test" not in (parsed.database or "").lower():
        pytest.fail("Vapi integration tests require a loopback test database")
    return url


def _event(call_id: str, event_type: str, **fields) -> dict:
    return {"message": {"type": event_type,
                        "call": {"id": call_id, "assistantId": "assistant-test", "startedAt": STARTED},
                        "timestamp": 1790325180000, **fields}}


def test_cached_tool_turn_never_reads_or_writes_database(monkeypatch) -> None:
    from claim_api.routes.voice import VapiToolWebhook, _next_intake_step

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")

    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError(f"Unexpected database call: {name}")

    tool = _event("cached-call", "tool-calls", toolCallList=[{
        "id": "cached-tool", "name": "next_intake_step", "parameters": {},
    }], artifact={"messages": [{"role": "user", "message": STORY}]})
    response = _next_intake_step(VapiToolWebhook.model_validate(tool),
                                 UUID("00000000-0000-4000-8000-000000000843"), NoDatabase())
    decision = json.loads(response["results"][0]["result"])
    assert decision["decision"] == "ask" and decision["case_id"] is None
    assert decision["next_question"] == "Quels sont votre prénom et votre nom ?"


def test_cached_tool_before_first_transcript_asks_for_story(monkeypatch) -> None:
    from claim_api.routes.voice import VapiToolWebhook, _next_intake_step

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")

    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError(f"Unexpected database call: {name}")

    tool = _event("early-tool", "tool-calls", toolCallList=[{
        "id": "early-tool-call", "name": "next_intake_step", "parameters": {},
    }], artifact={"messages": []})
    response = _next_intake_step(VapiToolWebhook.model_validate(tool),
                                 UUID("00000000-0000-4000-8000-000000000843"), NoDatabase())
    decision = json.loads(response["results"][0]["result"])
    assert decision["decision"] == "ask"
    assert decision["next_question"] == "Pouvez-vous raconter brièvement ce qui s'est passé ?"


@pytest.mark.parametrize("story", [
    "Je viens de rentrer dans une autre voiture.",
    "Je viens de me faire rentrer dans une voiture. Déjà par une voiture.",
])
def test_selected_p0_are_asked_once_before_completion(monkeypatch, story) -> None:
    from claim_api.vapi_native import cached_tool_turn

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "v20")
    messages = [{"role": "user", "message": story}]

    def turn(facts):
        message = _event("p0-regression", "tool-calls", artifact={"messages": messages})["message"]
        _, triage = cached_tool_turn(message, {"facts": facts})
        messages.append({"role": "tool_calls", "toolCalls": [{"function": {
            "name": "next_intake_step", "arguments": json.dumps({"facts": facts}),
        }}]})
        return triage

    # Preserve even an imperfect but sourced narrative rather than asking it again.
    first = turn([{"field": "narrative", "value": story, "excerpt": story}])
    assert first.status == "collecting"
    assert first.missing_p0 == ["location", "insured_name"]
    assert first.next_question == "Où l'accident s'est-il produit ?"

    messages.append({"role": "user", "message": "rue de Lyon à Paris"})
    second = turn([{"field": "location", "value": "rue de Lyon à Paris",
                    "excerpt": "rue de Lyon à Paris"}])
    assert second.status == "collecting"
    assert second.missing_p0 == ["insured_name"]
    assert second.next_question == "Quels sont votre prénom et votre nom ?"

    messages.append({"role": "user", "message": "Marie Martin"})
    final = turn([{"field": "insured_name", "value": "Marie Martin", "excerpt": "Marie Martin"}])
    assert final.status == "complete"
    assert final.missing_p0 == []
    assert final.next_question is None


def test_cached_tool_recognizes_just_happened_after_repeated_question(monkeypatch) -> None:
    from claim_api.vapi_native import cached_tool_turn

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")
    first = "Je viens de me faire rentrer dedans aux 46 boulevards de Port-Royal."
    recency = "Ça vient juste d'arriver."
    message = _event("real-call-regression", "tool-calls", artifact={"messages": [
        {"role": "user", "message": first, "startMs": 20000},
        {"role": "user", "message": recency, "startMs": 29000},
    ]})["message"]
    request, triage = cached_tool_turn(message, {"facts": [{
        "field": "incident_time", "value": "just_now", "excerpt": recency,
        "uncertainty": "none",
    }]})
    facts = {fact.field: fact for fact in request.facts}
    assert facts["incident_time"].value == "just_now"
    assert facts["incident_time"].uncertainty == "inferred"
    assert triage.next_question != "À quelle heure environ l'accident s'est-il produit ?"


def test_cached_tool_turn_uses_latest_caller_correction(monkeypatch) -> None:
    from claim_api.vapi_native import cached_tool_turn

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")
    message = _event("corrected-call", "tool-calls", artifact={"messages": [
        {"role": "user", "message": "L'accident était rue de Lyon à Paris."},
        {"role": "tool_calls", "toolCalls": [{"function": {"arguments": json.dumps({
            "facts": [{"field": "location", "value": "rue de Lyon à Paris",
                       "excerpt": "rue de Lyon à Paris"}],
        })}}]},
        {"role": "user", "message": "Pardon, c'était rue de Rivoli à Paris."},
    ]})["message"]
    request, _ = cached_tool_turn(message, {"facts": [{
        "field": "location", "value": "rue de Rivoli à Paris",
        "excerpt": "rue de Rivoli à Paris",
    }]})
    location = next(fact for fact in request.facts if fact.field == "location")
    assert location.value == "Rue de Rivoli, Paris"
    assert location.excerpt == "rue de Rivoli à Paris"


def test_cached_tool_turn_skips_relative_time_without_message_timestamp(monkeypatch) -> None:
    from claim_api.vapi_native import cached_tool_turn

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")
    message = _event("untimed-call", "tool-calls", artifact={"messages": [
        {"role": "user", "message": "Il s'est produit il y a une heure."},
    ]})["message"]
    request, triage = cached_tool_turn(message, {"facts": [{
        "field": "incident_time", "value": "minutes_ago:60",
        "excerpt": "il y a une heure", "uncertainty": "inferred",
    }]})
    assert all(fact.field != "incident_time" for fact in request.facts)
    assert triage.status == "collecting"


def test_web_call_normalization_keeps_browser_transport(monkeypatch) -> None:
    from claim_api.vapi_native import _normalized_request

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")
    message = _event("web-call-1", "transcript", role="user", transcriptType="final", transcript=STORY)["message"]
    message["call"]["type"] = "webCall"
    first = _normalized_request(message, None, "web-event-1")
    assert first.telephony_provider == "web"
    state = {"call_started_at": first.call_started_at, "sequence": 1,
             "transcript_json": [segment.model_dump(mode="json") for segment in first.segments],
             "facts_json": [fact.model_dump(mode="json") for fact in first.facts],
             "telephony_provider": "web"}
    report = _event("web-call-1", "end-of-call-report", artifact={"messages": []})["message"]
    later = _normalized_request(report, state, "web-event-2")
    assert later.telephony_provider == "web"


def test_report_restores_opening_from_call_artifact(monkeypatch) -> None:
    from claim_api.vapi_native import _final_messages, _normalized_request

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")
    user = _normalized_request(_event(
        "web-call-1", "transcript", role="user", transcriptType="final",
        transcript="Ouais, dites-moi ce qu'il faut faire.", startMs=17000,
    )["message"], None, "user-1")
    state = {"call_started_at": user.call_started_at, "sequence": 1,
             "transcript_json": [segment.model_dump(mode="json") for segment in user.segments],
             "facts_json": [], "telephony_provider": "web"}
    report = _event("web-call-1", "end-of-call-report", artifact={
        "messages": [{"role": "user", "message": user.segments[0].text, "startMs": 17000}],
        "transcript": "AI: Bonjour, je vais vous aider. User: Ouais, dites-moi ce qu'il faut faire.",
    })["message"]
    normalized = _normalized_request(report, state, "report-1")
    assert [(part.speaker, part.text) for part in normalized.segments] == [
        ("assistant", "Bonjour, je vais vous aider."), ("caller", user.segments[0].text)]
    assert normalized.segments[0].start_ms == 0

    # A configured firstMessage is insufficient evidence that it was spoken.
    report["artifact"].pop("transcript")
    report["assistant"] = {"firstMessage": "Bonjour, je vais vous aider."}
    assert [part["text"] for part in _final_messages(report)] == [user.segments[0].text]


def test_conversation_update_retains_opening_when_report_omits_it(monkeypatch) -> None:
    from claim_api.vapi_native import _normalized_request

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")
    first = _normalized_request(_event("web-call-1", "conversation-update", messages=[
        {"role": "system", "content": "Consignes de l'agent"},
        {"role": "assistant", "message": "Bonjour, comment puis-je vous aider ?"},
    ])["message"], None, "opening-1")
    assert first.segments[0].text == "Bonjour, comment puis-je vous aider ?"
    state = {"call_started_at": first.call_started_at, "sequence": 1,
             "transcript_json": [part.model_dump(mode="json") for part in first.segments],
             "facts_json": [], "telephony_provider": "web"}
    report = _event("web-call-1", "end-of-call-report", artifact={"messages": [
        {"role": "user", "message": "J'ai eu un accident.", "startMs": 17000},
    ]})["message"]
    normalized = _normalized_request(report, state, "report-1")
    assert [(part.speaker, part.text) for part in normalized.segments] == [
        ("assistant", "Bonjour, comment puis-je vous aider ?"),
        ("caller", "J'ai eu un accident."),
    ]


def test_live_vapi_events_with_created_at_only_build_case_before_tool_call(monkeypatch) -> None:
    from claim_api.vapi_native import _normalized_request, _segments_and_facts

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "assistant-v1")
    call = {"id": "web-call-1", "assistantId": "assistant-test", "type": "webCall",
            "createdAt": "2026-09-25T08:32:55Z"}
    speech = {"type": "assistant.speechStarted", "call": call, "timestamp": 1790325180000,
              "text": "Bonjour, comment puis-je vous aider ?", "turn": 0}
    first = _normalized_request(speech, None, "speech-1")
    assert first.telephony_provider == "web"
    assert [(segment.speaker, segment.text) for segment in first.segments] == [
        ("assistant", "Bonjour, comment puis-je vous aider ?")]

    state = {"call_started_at": first.call_started_at, "sequence": 1,
             "transcript_json": [segment.model_dump(mode="json") for segment in first.segments],
             "facts_json": [], "telephony_provider": "web"}
    final = {"type": "transcript", "call": call, "timestamp": 1790325181000,
             "role": "user", "transcriptType": "final", "transcript": STORY}
    second = _normalized_request(final, state, "final-1")
    assert [segment.speaker for segment in second.segments] == ["assistant", "caller"]
    assert second.event_type == "final_turn"

    updated_speech = {**speech, "timestamp": 1790325180500,
                      "text": "Bonjour, comment puis-je vous aider après votre accident ?"}
    segments, _ = _segments_and_facts(state, updated_speech, "speech-2")
    assert len(segments) == 1
    assert segments[0]["text"] == updated_speech["text"]


def test_end_report_keeps_speech_event_and_does_not_duplicate_combined_user_chunks(monkeypatch) -> None:
    from claim_api.vapi_native import _segments_and_facts

    monkeypatch.setenv("VOICE_ASSISTANT_ID", "assistant-test")
    call = {"id": "web-call-1", "assistantId": "assistant-test", "type": "webCall",
            "createdAt": "2026-09-25T08:32:55Z"}
    speech = {"type": "assistant.speechStarted", "call": call, "timestamp": 1790325180000,
              "text": "Bonjour.", "turn": 0}
    agent, _ = _segments_and_facts(None, speech, "speech-1")
    first = {"type": "transcript", "call": call, "timestamp": 1790325181000,
             "role": "user", "transcriptType": "final", "transcript": "J’ai eu un accident."}
    user, facts = _segments_and_facts({"transcript_json": agent, "facts_json": []}, first, "user-1")
    second = {**first, "timestamp": 1790325182000, "transcript": "Je suis à Paris."}
    user, facts = _segments_and_facts({"transcript_json": user, "facts_json": facts}, second, "user-2")
    report = {"type": "end-of-call-report", "call": call, "timestamp": 1790325190000,
              "artifact": {"messages": [{"role": "user", "message": "J’ai eu un accident. Je suis à Paris."}]}}
    full, _ = _segments_and_facts({"transcript_json": user, "facts_json": facts}, report, "report")
    assert [(segment["speaker"], segment["text"]) for segment in full] == [
        ("assistant", "Bonjour."), ("caller", "J’ai eu un accident."),
        ("caller", "Je suis à Paris.")]


def test_voice_agent_span_contains_only_safe_metadata(monkeypatch) -> None:
    import logfire
    from opentelemetry import trace
    from claim_api.routes.voice import _voice_agent_span

    recorded = []
    semantic_attributes = {}

    @contextmanager
    def span(name, **attributes):
        recorded.append((name, attributes))
        yield

    class CurrentSpan:
        def set_attribute(self, key, value):
            semantic_attributes[key] = value

    monkeypatch.setattr(logfire, "span", span)
    monkeypatch.setattr(trace, "get_current_span", lambda: CurrentSpan())
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-only-token")
    monkeypatch.setenv("VOICE_ASSISTANT_VERSION", "v9")
    with _voice_agent_span():
        pass
    assert recorded == [("voice.intake", {})]
    assert semantic_attributes == {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "Claimroom voice intake",
        "gen_ai.agent.version": "v9",
        "gen_ai.provider.name": "vapi",
        "openinference.span.kind": "AGENT",
    }


def test_report_preserves_repeated_utterances_without_replaying_final_events() -> None:
    from claim_api.vapi_native import _segments_and_facts

    first = _event("web-call-1", "transcript", role="user",
                   transcriptType="final", transcript="Oui")["message"]
    segments, facts = _segments_and_facts(None, first, "turn-1")
    state = {"transcript_json": segments, "facts_json": facts}
    segments, facts = _segments_and_facts(state, first, "turn-2")
    assert len(segments) == 2
    assert segments[0]["id"] != segments[1]["id"]

    report = _event("web-call-1", "end-of-call-report", artifact={"messages": [
        {"role": "assistant", "message": "Un autre véhicule était-il impliqué ?"},
        {"role": "user", "message": "Oui"},
        {"role": "assistant", "message": "Le conducteur est-il resté ?"},
        {"role": "user", "message": "Oui"},
    ]})["message"]
    full, _ = _segments_and_facts({"transcript_json": segments, "facts_json": facts}, report, "report")
    assert [(part["speaker"], part["text"]) for part in full] == [
        ("assistant", "Un autre véhicule était-il impliqué ?"),
        ("caller", "Oui"),
        ("assistant", "Le conducteur est-il resté ?"),
        ("caller", "Oui"),
    ]
    assert [part["id"] for part in full if part["speaker"] == "caller"] == [
        segments[0]["id"], segments[1]["id"]]


def test_native_web_call_creates_separate_browser_case(monkeypatch) -> None:
    database_url = _db_url()
    client = _configure(monkeypatch, database_url)
    call_id = f"vapi-web-{uuid4()}"
    headers = {"X-Vapi-Secret": "test-business-secret"}
    first_event = _event(call_id, "transcript", role="user", transcriptType="final", transcript=STORY)
    first_event["message"]["call"]["type"] = "webCall"
    first_event["message"]["call"].pop("startedAt")
    first_event["message"]["call"]["createdAt"] = "2026-09-25T08:32:55Z"
    first = client.post("/v1/voice/vapi/server-events", headers=headers, json=first_event)
    assert first.status_code == 200, first.text
    assert first.json() == {"ignored": True}
    owner = UUID("00000000-0000-4000-8000-000000000843")
    repository = VoiceIntakeRepository(database_url)
    assert repository.get_session(owner, call_id) is None

    report = _event(call_id, "end-of-call-report", artifact={"messages": [
        {"role": "user", "message": STORY},
    ]})
    report["message"]["call"].pop("startedAt")
    report["message"]["call"].update(type="webCall", createdAt="2026-09-25T08:32:55Z")
    second = client.post("/v1/voice/vapi/server-events", headers=headers, json=report)
    assert second.status_code == 200, second.text
    assert second.json()["session"]["telephony_provider"] == "web"
    case = repository.get_case(owner, UUID(second.json()["session"]["case_id"]))
    assert case.scenario_id == "voice_web"
    assert case.voice_session is not None and case.voice_session.telephony_provider == "web"


def test_native_vapi_transcript_tool_report_flow_is_one_case(monkeypatch) -> None:
    database_url = _db_url()
    client = _configure(monkeypatch, database_url)
    call_id = f"vapi-{uuid4()}"
    headers = {"X-Vapi-Secret": "test-business-secret"}
    complete_story = f"{STORY} Je suis Marie Martin."

    partial = client.post("/v1/voice/vapi/server-events", headers=headers, json=_event(
        call_id, "transcript", role="user", transcriptType="partial", transcript="Je viens...",
    ))
    assert partial.status_code == 200 and partial.json() == {"ignored": True}
    owner = UUID("00000000-0000-4000-8000-000000000843")
    started = VoiceIntakeRepository(database_url).get_session(owner, call_id)
    assert started is None

    final_payload = _event(call_id, "transcript", role="user", transcriptType="final", transcript=complete_story)
    first = client.post("/v1/voice/vapi/server-events", headers=headers, json=final_payload)
    assert first.status_code == 200, first.text
    assert first.json() == {"ignored": True}
    assert VoiceIntakeRepository(database_url).get_session(owner, call_id) is None

    replay = client.post("/v1/voice/vapi/server-events", headers=headers, json=final_payload)
    assert replay.json() == {"ignored": True}

    tool_payload = _event(call_id, "tool-calls", toolCallList=[{
        "id": "tool-1", "type": "function", "function": {
            "name": "next_intake_step", "arguments": {"provider_call_id": "forged-id", "facts": [{
            "field": "location", "value": "rue de Lyon à Paris", "excerpt": "rue de Lyon à Paris",
            }, {
            "field": "insured_name", "value": "Marie Martin", "excerpt": "Marie Martin",
            }]},
        },
    }], artifact={"messages": [{"role": "user", "message": complete_story}]})
    decision = client.post("/v1/voice/vapi/next-intake-step", headers=headers, json=tool_payload)
    assert decision.status_code == 200, decision.text
    tool_result = json.loads(decision.json()["results"][0]["result"])
    assert tool_result["decision"] == "complete"
    assert tool_result["next_question"] is None
    assert tool_result["missing_fields"] == []
    assert tool_result["case_id"] is None
    assert VoiceIntakeRepository(database_url).get_session(owner, call_id) is None

    report_payload = _event(call_id, "end-of-call-report", artifact={
        "messages": [{"role": "assistant", "message": "Bonjour"},
                     {"role": "user", "message": complete_story},
                     {"role": "tool_calls", "toolCalls": [{"function": {"arguments": {
                         "facts": [{"field": "insured_name", "value": "Marie Martin",
                                    "excerpt": "Marie Martin"}],
                     }}}]}],
        "recording": {"url": "https://example.invalid/private-recording"},
    })
    end = client.post("/v1/voice/vapi/server-events", headers=headers, json=report_payload)
    assert end.status_code == 200 and end.json()["session"]["triage"]["status"] == "complete"
    assert client.post("/v1/voice/vapi/server-events", headers=headers, json=report_payload).json()["replayed"]

    case_id = end.json()["session"]["case_id"]
    case = VoiceIntakeRepository(database_url).get_case(owner, UUID(case_id))
    assert case.voice_session is not None and case.voice_session.status == "complete"
    assert case.intake.time_source == "inferred_from_call"
    assert case.intake.location == "Rue de Lyon, Paris"
    assert case.intake.insured_name == "Marie Martin"
    assert case.state_version == 1
    assert len([event for event in case.timeline if event.event_type == "case.voice_intake_recorded"]) == 1
    assert [(item["speaker"], item["text"]) for item in case.voice_session.segments] == [
        ("assistant", "Bonjour"), ("caller", complete_story)]
    assert case.voice_session.recording["status"] == "error"
    assert all("private-recording" not in json.dumps(event.metadata) for event in case.timeline)


@pytest.mark.parametrize("partial_first", [False, True])
def test_tool_can_bootstrap_before_final_transcript_without_duplicate(
    monkeypatch, partial_first: bool,
) -> None:
    client = _configure(monkeypatch, _db_url())
    call_id = f"vapi-{uuid4()}"
    headers = {"X-Vapi-Secret": "test-business-secret"}
    created_at = "2026-09-25T08:33:00Z"
    spoken_at = 1790325192000
    spoken = "Je viens d'avoir un accident rue de Lyon à Paris."
    tool = _event(call_id, "tool-calls", toolCallList=[{
        "id": "tool-before-transcript", "type": "function", "function": {
            "name": "next_intake_step", "arguments": {"facts": [{
                "field": "location", "value": "rue de Lyon à Paris",
                "excerpt": "rue de Lyon à Paris",
            }]},
        },
    }], artifact={"messages": [
        {"role": "system", "message": "Instructions"},
        {"role": "user", "message": spoken, "time": spoken_at},
    ]})
    tool["message"]["call"].pop("startedAt")
    tool["message"]["call"].update(createdAt=created_at, type="inboundPhoneCall")
    if partial_first:
        partial = _event(call_id, "transcript", role="user", transcriptType="partial",
                         transcript="Je viens...")
        partial["message"]["call"] = tool["message"]["call"]
        for _ in range(2):
            response = client.post("/v1/voice/vapi/server-events", headers=headers, json=partial)
            assert response.status_code == 200, response.text
    decision = client.post("/v1/voice/vapi/next-intake-step", headers=headers, json=tool)
    assert decision.status_code == 200, decision.text
    answer = json.loads(decision.json()["results"][0]["result"])
    assert answer["decision"] == "ask"
    assert answer["missing_fields"] == ["insured_name"]
    assert answer["next_question"] == "Quels sont votre prénom et votre nom ?"
    assert answer["case_id"] is None
    owner = UUID("00000000-0000-4000-8000-000000000843")
    state = VoiceIntakeRepository(_db_url()).get_state(owner, call_id)
    assert state is None

    final = _event(call_id, "transcript", role="user", transcriptType="final",
                   transcript=spoken)
    final["message"]["call"].pop("startedAt")
    final["message"]["call"].update(createdAt=created_at, type="inboundPhoneCall")
    final["message"]["timestamp"] = spoken_at + 8
    received = client.post("/v1/voice/vapi/server-events", headers=headers, json=final)
    assert received.status_code == 200, received.text
    assert received.json() == {"ignored": True}
    report = _event(call_id, "end-of-call-report", artifact={"messages": [
        {"role": "user", "message": spoken, "time": spoken_at},
        {"role": "tool_calls", "toolCalls": [{"function": {"arguments": json.dumps({
            "facts": [{"field": "location", "value": "rue de Lyon à Paris",
                       "excerpt": "rue de Lyon à Paris"}],
        })}}]},
    ]})
    report["message"]["call"] = tool["message"]["call"]
    saved = client.post("/v1/voice/vapi/server-events", headers=headers, json=report)
    assert saved.status_code == 200, saved.text
    case = VoiceIntakeRepository(_db_url()).get_case(owner, UUID(saved.json()["session"]["case_id"]))
    assert [(item["speaker"], item["text"]) for item in case.voice_session.segments] == [
        ("caller", spoken)]
    assert any(fact["field"] == "location" for fact in case.voice_session.facts)
    assert client.post("/v1/voice/vapi/server-events", headers=headers, json=report).json()["replayed"]


def test_native_end_report_without_transcript_creates_incomplete_session(monkeypatch) -> None:
    client = _configure(monkeypatch, _db_url())
    response = client.post("/v1/voice/vapi/server-events",
                           headers={"X-Vapi-Secret": "test-business-secret"},
                           json=_event(f"vapi-{uuid4()}", "end-of-call-report",
                                       artifact={"messages": []}, endedReason="hangup"))
    assert response.status_code == 200
    assert response.json()["session"]["triage"]["status"] == "incomplete"


def test_native_event_rejects_unexpected_assistant_and_ignores_unsourced_tool_fact(monkeypatch) -> None:
    client = _configure(monkeypatch, _db_url())
    call_id = f"vapi-{uuid4()}"
    headers = {"X-Vapi-Secret": "test-business-secret"}
    wrong = _event(call_id, "transcript", role="user", transcriptType="final", transcript=STORY)
    wrong["message"]["call"]["assistantId"] = "other-assistant"
    rejected = client.post("/v1/voice/vapi/server-events", headers=headers, json=wrong)
    assert rejected.status_code == 403

    client.post("/v1/voice/vapi/server-events", headers=headers, json=_event(
        call_id, "transcript", role="user", transcriptType="final", transcript=STORY,
    ))
    tool = _event(call_id, "tool-calls", toolCallList=[{
        "id": "forged-tool", "name": "next_intake_step", "parameters": {"facts": [{
            "field": "location", "value": "Londres", "excerpt": "rue de Lyon à Paris",
        }]},
    }], artifact={"messages": [{"role": "user", "message": STORY}]})
    response = client.post("/v1/voice/vapi/next-intake-step", headers=headers, json=tool)
    assert response.status_code == 200
    assert json.loads(response.json()["results"][0]["result"])["decision"] == "ask"
    owner = UUID("00000000-0000-4000-8000-000000000843")
    assert VoiceIntakeRepository(_db_url()).get_state(owner, call_id) is None
    report = _event(call_id, "end-of-call-report", artifact={"messages": [
        {"role": "user", "message": STORY},
        {"role": "tool_calls", "toolCalls": [{"function": {"arguments": json.dumps({
            "facts": [{"field": "location", "value": "Londres", "excerpt": "rue de Lyon à Paris"}],
        })}}]},
    ]})
    saved = client.post("/v1/voice/vapi/server-events", headers=headers, json=report)
    assert saved.status_code == 200, saved.text
    state = VoiceIntakeRepository(_db_url()).get_state(owner, call_id)
    assert all(fact["value"] != "Londres" for fact in state["facts_json"])


def test_native_tool_accepts_one_hour_as_sourced_relative_time(monkeypatch) -> None:
    client = _configure(monkeypatch, _db_url())
    call_id = f"vapi-{uuid4()}"
    headers = {"X-Vapi-Secret": "test-business-secret"}
    final = _event(call_id, "transcript", role="user", transcriptType="final",
                   transcript="Il s'est produit il y a une heure.")
    assert client.post("/v1/voice/vapi/server-events", headers=headers, json=final).status_code == 200
    tool = _event(call_id, "tool-calls", toolCallList=[{
        "id": "one-hour-tool", "name": "next_intake_step", "parameters": {"facts": [{
            "field": "incident_time", "value": "minutes_ago:60",
            "excerpt": "il y a une heure", "uncertainty": "inferred",
        }]},
    }], artifact={"messages": [{"role": "user", "message": "Il s'est produit il y a une heure.",
                               "startMs": 1000}]})
    response = client.post("/v1/voice/vapi/next-intake-step", headers=headers, json=tool)
    assert response.status_code == 200, response.text
    assert json.loads(response.json()["results"][0]["result"])["decision"] == "ask"
    owner = UUID("00000000-0000-4000-8000-000000000843")
    state = VoiceIntakeRepository(_db_url()).get_state(owner, call_id)
    assert state is None
    report = _event(call_id, "end-of-call-report", artifact={"messages": [
        {"role": "user", "message": "Il s'est produit il y a une heure.", "startMs": 1000},
        {"role": "tool_calls", "toolCalls": [{"function": {"arguments": json.dumps({
            "facts": [{"field": "incident_time", "value": "minutes_ago:60",
                       "excerpt": "il y a une heure", "uncertainty": "inferred"}],
        })}}]},
    ]})
    saved = client.post("/v1/voice/vapi/server-events", headers=headers, json=report)
    assert saved.status_code == 200, saved.text
    state = VoiceIntakeRepository(_db_url()).get_state(owner, call_id)
    assert next(fact for fact in state["facts_json"] if fact["field"] == "incident_time")["value"] == "minutes_ago:60"
