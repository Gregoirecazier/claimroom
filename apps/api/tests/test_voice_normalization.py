from datetime import datetime, timezone
from uuid import UUID

from claim_api.vapi_native import _normalized_request
from claim_api.voice_intake import (
    TranscriptSegmentV1, VoiceIngestResponse, VoiceIntakeRequestV1, VoiceSessionView,
    deterministic_facts, process_voice_intake,
)
from claim_api.voice_normalization import minutes_ago, normalize_address
from claim_api.voice_repository import VoiceIntakeRepository


def test_spoken_relative_time_and_address_are_extracted_from_final_turn() -> None:
    message = {
        "type": "transcript", "transcriptType": "final", "role": "user",
        "transcript": "Ça s'est passé il y a dix minutes, au 12 rue Victor Hugo à Paris.",
        "timestamp": "2026-09-25T10:40:00+02:00",
        "call": {"id": "test-call", "assistantId": "test-assistant",
                 "startedAt": "2026-09-25T10:33:00+02:00"},
    }
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        patch.setenv("VOICE_ASSISTANT_ID", "test-assistant")
        patch.setenv("VOICE_ASSISTANT_VERSION", "v10")
        request = _normalized_request(message, None, "final-1")
    facts = {fact.field: fact for fact in request.facts}
    assert facts["incident_time"].value == "minutes_ago:10"
    assert facts["location"].value == "12 Rue Victor Hugo, Paris"
    intake, triage = process_voice_intake(request)
    assert intake.incident_at == datetime(2026, 9, 25, 8, 30, tzinfo=timezone.utc)
    assert intake.time_source == "inferred_from_call"
    assert intake.location == "12 Rue Victor Hugo, Paris"
    assert {"incident_time", "location"}.isdisjoint(triage.missing_p0)


def test_last_call_phrasing_extracts_sourced_facts_and_flags_ambiguous_place() -> None:
    from pytest import MonkeyPatch
    turns = [
        ("user", "Bah oui, j'aimerais bien savoir s'il y a des démarches à faire.", 12000),
        ("assistant", "Pouvez-vous raconter brièvement ce qui s'est passé ?", 17000),
        ("user", "J'étais en train de conduire, il y a une voiture qui me rentrait dedans par l'arrière.", 26000),
        ("assistant", "Pouvez-vous me préciser où l'accident a eu lieu ?", 37000),
        ("user", "Il y a eu cinq minutes.", 44000),
        ("assistant", "Pouvez-vous indiquer où l'accident a eu lieu ?", 49000),
        ("user", "Il y a eu deux places de l'étoile.", 56000),
    ]
    message = {
        "type": "end-of-call-report",
        "artifact": {"messages": [
            {"role": role, "message": text, "startMs": start_ms}
            for role, text, start_ms in turns
        ]},
        "call": {"id": "last-call", "assistantId": "test-assistant",
                 "startedAt": "2026-09-26T15:37:00+02:00"},
    }
    with MonkeyPatch.context() as patch:
        patch.setenv("VOICE_ASSISTANT_ID", "test-assistant")
        patch.setenv("VOICE_ASSISTANT_VERSION", "v11")
        request = _normalized_request(message, None, "last-call-report")
    intake, triage = process_voice_intake(request)
    facts = {fact.field: fact for fact in request.facts}
    assert set(facts) == {"narrative", "third_party_involved", "incident_time", "location"}
    assert facts["incident_time"].value == "minutes_ago:5"
    assert facts["location"].uncertainty == "uncertain"
    assert intake.location is None
    assert intake.incident_at == datetime(2026, 9, 26, 13, 32, 44, tzinfo=timezone.utc)
    assert triage.missing_p0 == ["location", "insured_name"]


def test_reextract_legacy_call_uses_existing_offsets_without_overwriting_corrections() -> None:
    from uuid import uuid4

    actor_id, case_id = uuid4(), uuid4()
    row = {
        "provider_call_id": "existing-call", "triage_json": {"status": "incomplete"},
        "facts_json": [], "transcript_json": [
            {"id": "old-1", "speaker": "caller", "text": "Une voiture qui me rentrait dedans par l'arrière.",
             "start_ms": 26000, "end_ms": None},
            {"id": "old-2", "speaker": "caller", "text": "Il y a eu cinq minutes.",
             "start_ms": 44000, "end_ms": None},
        ],
        "telephony_provider": "web", "speech_provider": "gradium", "sequence": 3,
        "call_started_at": datetime.fromisoformat("2026-09-26T15:37:00+02:00"),
        "assistant_id": "test-assistant", "assistant_version": "v11", "mode": "live",
        "projected_intake_json": {},
    }

    class Repository(VoiceIntakeRepository):
        def __init__(self) -> None:
            self.ingested: VoiceIntakeRequestV1 | None = None

        def get_case_recording(self, owner: UUID, requested_case_id: UUID) -> dict | None:
            return row if owner == actor_id and requested_case_id == case_id else None

        def event_exists(self, owner: UUID, call_id: str, key: str) -> bool:
            return False

        def ingest(self, owner: UUID, candidate: VoiceIntakeRequestV1) -> VoiceIngestResponse:
            self.ingested = candidate
            _, triage = process_voice_intake(candidate)
            return VoiceIngestResponse(session=VoiceSessionView(
                case_id=case_id, provider="vapi", provider_call_id=candidate.provider_call_id,
                sequence=candidate.sequence, mode=candidate.mode, telephony_provider=candidate.telephony_provider,
                triage=triage, facts=candidate.facts, call_started_at=candidate.call_started_at,
                assistant_id=candidate.assistant_id, assistant_version=candidate.assistant_version,
                extractor_version=candidate.extractor_version,
            ), replayed=False)

    repository = Repository()
    result = repository.reextract(actor_id, case_id)
    assert result is not None and result.session.sequence == 4
    assert {fact.field for fact in result.session.facts} == {"narrative", "third_party_involved", "incident_time"}
    assert repository.ingested is not None
    assert repository.ingested.segments[1].observed_at == datetime(2026, 9, 26, 13, 37, 44, tzinfo=timezone.utc)
    assert repository.reextract(uuid4(), case_id) is None


def test_relative_time_anchor_survives_later_events() -> None:
    payload = {
        "schema_version": 1, "provider_call_id": "test-call", "sequence": 2,
        "source_event_key": "report", "event_type": "end_of_call_report",
        "call_started_at": "2026-09-25T10:33:00+02:00",
        "received_at": "2026-09-25T10:55:00+02:00",
        "assistant_id": "test-assistant", "assistant_version": "v10",
        "extractor_version": "voice-rules-v2",
        "segments": [{"id": "caller-1", "speaker": "caller",
                      "text": "Il y a 10 minutes.",
                      "observed_at": "2026-09-25T10:40:00+02:00"}],
        "facts": [{"field": "incident_time", "value": "minutes_ago:10",
                   "segment_id": "caller-1", "excerpt": "Il y a 10 minutes.",
                   "uncertainty": "inferred"}],
    }
    intake, _ = process_voice_intake(VoiceIntakeRequestV1.model_validate(payload))
    assert intake.incident_at == datetime(2026, 9, 25, 8, 30, tzinfo=timezone.utc)


def test_untimed_end_report_does_not_date_a_relative_statement() -> None:
    message = {
        "type": "end-of-call-report",
        "artifact": {"messages": [{"role": "user", "message": "Il y a dix minutes."}]},
        "call": {"id": "test-call", "assistantId": "test-assistant",
                 "startedAt": "2026-09-25T10:33:00+02:00"},
    }
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        patch.setenv("VOICE_ASSISTANT_ID", "test-assistant")
        patch.setenv("VOICE_ASSISTANT_VERSION", "v10")
        request = _normalized_request(message, None, "report-1")
    assert not any(fact.field == "incident_time" for fact in request.facts)
    intake, triage = process_voice_intake(request)
    assert intake.incident_at is None
    assert "incident_time" not in triage.missing_p0
    assert "insured_name" in triage.missing_p0


def test_optional_identity_and_contract_details_are_projected_when_stated() -> None:
    text = "Je m'appelle Marie Martin, ma référence est DOS-42 et mon contrat est POL-58."
    payload = {
        "schema_version": 1, "provider_call_id": "test-call", "sequence": 1,
        "source_event_key": "final", "call_started_at": "2026-09-25T10:33:00+02:00",
        "received_at": "2026-09-25T10:33:00+02:00",
        "assistant_id": "test-assistant", "assistant_version": "v10",
        "extractor_version": "voice-rules-v2",
        "segments": [{"id": "caller-1", "speaker": "caller", "text": text}],
        "facts": [
            {"field": field, "value": value, "segment_id": "caller-1", "excerpt": text}
            for field, value in (("insured_name", "Marie Martin"),
                                 ("insured_reference", "DOS-42"),
                                 ("policy_reference", "POL-58"))
        ],
    }
    intake, _ = process_voice_intake(VoiceIntakeRequestV1.model_validate(payload))
    assert (intake.insured_name, intake.insured_reference, intake.policy_reference) == (
        "Marie Martin", "DOS-42", "POL-58")


def test_uncertain_duration_or_street_without_name_is_not_invented() -> None:
    assert minutes_ago("Il s'est produit il y a une heure") == 60
    assert minutes_ago("Ça s'est passé il y a deux heures") == 120
    assert minutes_ago("C'était il y a une dizaine de minutes") is None
    assert minutes_ago("C'était il y a quatre-vingt-dix minutes") is None
    assert normalize_address("J'ai traversé la rue.") is None
    facts = deterministic_facts(TranscriptSegmentV1(
        id="caller-1", speaker="caller",
        text="Je viens de Paris, mais l'accident s'est passé hier.",
    ))
    assert not any(fact.field == "incident_time" for fact in facts)


def test_unknown_time_and_location_remain_missing_without_repeated_question() -> None:
    text = "Je ne sais pas où c'était, et je ne connais pas l'heure."
    payload = {
        "schema_version": 1, "provider_call_id": "test-call", "sequence": 1,
        "source_event_key": "final", "call_started_at": "2026-09-25T10:33:00+02:00",
        "received_at": "2026-09-25T10:33:00+02:00",
        "assistant_id": "test-assistant", "assistant_version": "v10",
        "extractor_version": "voice-rules-v2",
        "segments": [{"id": "caller-1", "speaker": "caller", "text": text}],
        "facts": [
            {"field": field, "value": "unknown", "segment_id": "caller-1", "excerpt": text}
            for field in ("location", "incident_time")
        ],
    }
    intake, triage = process_voice_intake(VoiceIntakeRequestV1.model_validate(payload))
    assert intake.incident_at is None and intake.location is None
    assert "location" in triage.missing_p0
    assert "incident_time" not in triage.missing_p0
    assert "insured_name" in triage.missing_p0
    assert triage.next_question != "Où l'accident s'est-il produit ?"


def test_one_invalid_tool_candidate_does_not_discard_other_stated_facts(monkeypatch) -> None:
    from claim_api import vapi_native

    class Repository:
        def get_state(self, _actor, _call):
            return {
                "transcript_json": [{"id": "caller-1", "speaker": "caller",
                                     "text": "Ma référence est DOS-42. Je suis au 12 rue Victor Hugo à Paris."}],
                "facts_json": [], "telephony_provider": "web", "sequence": 1,
                "call_started_at": datetime(2026, 9, 25, 8, 33, tzinfo=timezone.utc),
                "assistant_id": "test-assistant", "assistant_version": "v10",
            }

        def event_exists(self, _actor, _call, _key):
            return False

    captured = []

    def ingest(_repository, _actor, request):
        captured.append(request)
        _, triage = process_voice_intake(request)
        session = VoiceSessionView(
            case_id=UUID("00000000-0000-4000-8000-000000000001"),
            provider="vapi", provider_call_id="test-call", sequence=request.sequence,
            mode="live", telephony_provider="web", triage=triage, facts=request.facts,
            call_started_at=request.call_started_at, assistant_version="v10",
            assistant_id="test-assistant", extractor_version="voice-rules-v2",
        )
        return VoiceIngestResponse(session=session, replayed=False)

    monkeypatch.setattr(vapi_native, "_ingest", ingest)
    session = vapi_native.apply_tool_facts(
        Repository(), UUID("00000000-0000-4000-8000-000000000001"),
        "test-call", "tool-1", {"facts": [
            {"field": "insured_reference", "value": "DOS-42", "excerpt": "DOS-42"},
            {"field": "location", "value": "Londres", "excerpt": "12 rue Victor Hugo à Paris"},
        ]},
    )
    assert session is not None
    assert [(fact.field, fact.value) for fact in session.facts] == [
        ("insured_reference", "DOS-42")]
    assert len(captured) == 1
