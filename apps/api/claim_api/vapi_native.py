"""Translate authenticated native Vapi server events to the S08 contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from claim_api.agent_tracing import diagnostic_span
from claim_api.routes.voice import VoiceOwner, VoiceRepositoryDependency, _auto_fake_follow_up, _ingest
from claim_api.voice_intake import (
    TranscriptSegmentV1, VoiceFactV1, VoiceIngestResponse, VoiceIntakeRequestV1,
    VoiceSessionView, _assert_fact_supported, deterministic_facts, restore_segment_time,
    process_voice_intake,
)
from claim_api.voice_repository import VoiceIntakeRepository
from claim_api.voice_normalization import normalize_address


router = APIRouter(prefix="/v1/voice/vapi", tags=["voice-intake"])


def _message(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message")
    if not isinstance(message, dict):
        raise HTTPException(422, detail={"code": "invalid_vapi_event", "message": "Vapi message required.", "details": {}})
    return message


def _call_context(message: dict[str, Any]) -> tuple[str, str, datetime]:
    call = message.get("call")
    if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]:
        raise HTTPException(422, detail={"code": "invalid_vapi_event", "message": "Vapi call context required.", "details": {}})
    assistant = message.get("assistant") if isinstance(message.get("assistant"), dict) else {}
    call_assistant = call.get("assistantId")
    assistant_id = assistant.get("id")
    expected = os.getenv("VOICE_ASSISTANT_ID", "")
    if not expected or (call_assistant is not None and call_assistant != expected) or (assistant_id is not None and assistant_id != expected):
        raise HTTPException(403, detail={"code": "voice_assistant_mismatch", "message": "Unexpected assistant.", "details": {}})
    if call_assistant is None and assistant_id is None:
        raise HTTPException(403, detail={"code": "voice_assistant_mismatch", "message": "Assistant identity missing.", "details": {}})
    # Vapi's in-call transcript webhooks carry createdAt but often omit
    # startedAt. Use the immutable creation time for every event of one call.
    started = call.get("createdAt") or call.get("startedAt") or message.get("startedAt")
    if not isinstance(started, str):
        raise HTTPException(422, detail={"code": "invalid_vapi_event", "message": "Call start time required.", "details": {}})
    try:
        parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(422, detail={"code": "invalid_vapi_event", "message": "Invalid call start time.", "details": {}}) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(422, detail={"code": "invalid_vapi_event", "message": "Call start time needs a timezone.", "details": {}})
    return call["id"], expected, parsed.astimezone(timezone.utc)


def _event_key(message: dict[str, Any]) -> str:
    # Hash the native event, excluding phone number/audio from stored metadata.
    digest = hashlib.sha256(json.dumps(
        message, ensure_ascii=False, sort_keys=True, default=str,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"vapi:{message.get('type', 'unknown')}:{digest}"


def _final_messages(message: dict[str, Any]) -> list[dict[str, Any]]:
    event_type = message.get("type")
    if event_type == "conversation-update":
        # Vapi can commit the first greeting without sending a final transcript
        # for it. Later updates repeat the full history, so keep only turn one.
        items = message.get("messages")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict) or item.get("role") == "system":
                    continue
                content = item.get("message", item.get("content"))
                if item.get("role") == "assistant" and isinstance(content, str) and content.strip():
                    return [{"role": "assistant", "text": content.strip(), "start_ms": 0}]
                break
        return []
    if event_type == "assistant.speechStarted":
        spoken = message.get("text")
        turn = message.get("turn")
        if isinstance(spoken, str) and spoken.strip() and isinstance(turn, int) and turn >= 0:
            return [{"role": "assistant", "text": spoken, "turn": turn}]
        return []
    if event_type in {"transcript", 'transcript[transcriptType="final"]'}:
        if message.get("role") not in {"user", "customer", "assistant"}:
            return []
        if event_type == "transcript" and message.get("transcriptType") != "final":
            return []
        text = message.get("transcript")
        return [{"role": message["role"], "text": text,
                 "start_ms": message.get("startMs"), "end_ms": message.get("endMs")}] if isinstance(text, str) and text.strip() else []
    if event_type == "end-of-call-report":
        artifact = message.get("artifact")
        items = artifact.get("messages") if isinstance(artifact, dict) else None
        if not isinstance(items, list):
            items = []
        call = message.get("call") if isinstance(message.get("call"), dict) else {}
        started = call.get("createdAt") or call.get("startedAt")
        started_ms = None
        if isinstance(started, str):
            try:
                started_ms = datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp() * 1000
            except ValueError:
                pass
        turns = [{"role": item["role"], "text": item["message"],
                 "start_ms": item.get("startMs") if isinstance(item.get("startMs"), int)
                 else max(0, int(item["time"] - started_ms))
                 if started_ms is not None and isinstance(item.get("time"), (int, float)) else None,
                 "end_ms": item.get("endMs")}
                for item in items if isinstance(item, dict)
                and item.get("role") in {"user", "customer", "assistant"}
                and isinstance(item.get("message"), str) and item["message"].strip()]
        if isinstance(artifact, dict) and (not turns or turns[0]["role"] != "assistant"):
            opening = _artifact_opening(artifact)
            if opening and not any(turn["role"] == "assistant" and turn["text"] == opening for turn in turns):
                turns.insert(0, {"role": "assistant", "text": opening, "start_ms": 0})
        return turns
    return []


def cached_tool_turn(message: dict[str, Any], arguments: dict[str, Any]) -> tuple[VoiceIntakeRequestV1, Any]:
    """Rebuild one decision from Vapi's call artifact without a database round trip."""
    artifact = message.get("artifact")
    if not isinstance(artifact, dict) or not isinstance(artifact.get("messages"), list):
        raise ValueError("A Vapi call artifact is required")
    synthetic = {"type": "end-of-call-report", "call": message.get("call"),
                 "artifact": artifact, "timestamp": message.get("timestamp")}
    request = _normalized_request(synthetic, None, "vapi:cached-tool")
    # Vapi can invoke the tool before its first caller transcript is committed.
    # An empty artifact is a valid in-call state, not a final transcript turn.
    request = request.model_copy(update={
        "event_type": "final_turn" if request.segments else "call_started",
    })
    candidates = _artifact_fact_candidates(artifact)
    if isinstance(arguments.get("facts"), list):
        candidates.extend(arguments["facts"])
    facts, accepted = _merge_sourced_facts(request.segments, request.facts, candidates)
    with diagnostic_span("voice.tool.project_cached_turn", proposed_count=len(candidates),
                         accepted_count=accepted):
        request = VoiceIntakeRequestV1.model_validate({
            **request.model_dump(mode="json"), "facts": facts,
        })
        _, triage = process_voice_intake(request)
    return request, triage


def _artifact_fact_candidates(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in artifact.get("messages", []):
        if not isinstance(item, dict) or item.get("role") != "tool_calls":
            continue
        calls = item.get("toolCalls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            proposed = function.get("arguments")
            if isinstance(proposed, str):
                try:
                    proposed = json.loads(proposed)
                except ValueError:
                    continue
            if isinstance(proposed, dict) and isinstance(proposed.get("facts"), list):
                candidates.extend(proposed["facts"])
    return candidates


def _candidate_uncertainty(candidate: dict[str, Any]) -> Any:
    uncertainty = candidate.get("uncertainty", "explicit")
    if uncertainty in (None, "", "none"):
        value = candidate.get("value")
        if candidate.get("field") == "incident_time" and isinstance(value, str) and (
                value == "just_now" or value.startswith("minutes_ago:")):
            return "inferred"
        return "explicit"
    return uncertainty


def _merge_sourced_facts(segments: list, initial: list, candidates: list) -> tuple[list[dict], int]:
    facts = {}
    for raw in initial:
        fact = VoiceFactV1.model_validate(raw)
        facts[fact.field] = fact.model_dump(mode="json")
    callers = [TranscriptSegmentV1.model_validate(segment) for segment in segments
               if (segment.speaker if isinstance(segment, TranscriptSegmentV1)
                   else segment.get("speaker")) == "caller"]
    accepted = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        excerpt = candidate.get("excerpt")
        if not isinstance(excerpt, str) or not excerpt:
            continue
        source = next((segment for segment in reversed(callers)
                       if segment.speaker == "caller" and excerpt in segment.text), None)
        if source is None:
            continue
        if (candidate.get("field") == "incident_time"
                and isinstance(candidate.get("value"), str)
                and candidate["value"].startswith("minutes_ago:")
                and source.observed_at is None):
            continue
        try:
            fact = VoiceFactV1.model_validate({
                "field": candidate.get("field"), "value": candidate.get("value"),
                "segment_id": source.id, "excerpt": excerpt,
                "uncertainty": _candidate_uncertainty(candidate),
            })
            _assert_fact_supported(fact)
            if fact.field == "location":
                normalized = normalize_address(fact.value)
                if normalized:
                    fact = fact.model_copy(update={"value": normalized})
        except ValueError:
            continue
        facts[fact.field] = fact.model_dump(mode="json")
        accepted += 1
    return list(facts.values()), accepted


def _artifact_opening(artifact: dict[str, Any]) -> str | None:
    """Recover only greeting text actually present in a Vapi call artifact."""
    for key, field in (("messagesOpenAIFormatted", "content"), ("transcript", "message")):
        items = artifact.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("role") == "system":
                continue
            content = item.get(field)
            if item.get("role") == "assistant" and isinstance(content, str) and content.strip():
                return content.strip()
            break
    transcript = artifact.get("transcript")
    if isinstance(transcript, str):
        match = re.match(r"\s*(?:AI|Assistant|Agent):\s*(.+?)(?=\s+(?:User|Customer|Appelant|AI|Assistant|Agent):|$)",
                         transcript, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip() or None
    return None


def _segments_and_facts(state: dict | None, message: dict[str, Any], key: str,
                        observed_at: datetime | None = None,
                        call_started_at: datetime | None = None) -> tuple[list[dict], list[dict]]:
    segments = [dict(segment) for segment in state["transcript_json"]] if state else []
    facts = {fact["field"]: fact for fact in (state["facts_json"] if state else [])}
    call = message.get("call") if isinstance(message.get("call"), dict) else {}
    start = call.get("createdAt") or call.get("startedAt")
    timestamp = message.get("timestamp")
    event_start_ms = None
    if (message.get("type") != "end-of-call-report"
            and isinstance(start, str) and isinstance(timestamp, (int, float))):
        try:
            event_start_ms = max(0, int(timestamp - datetime.fromisoformat(
                start.replace("Z", "+00:00")).timestamp() * 1000))
        except ValueError:
            pass
    def add_segment(item: dict[str, Any], index: int) -> dict:
        text = item["text"]
        speaker = "caller" if item["role"] in {"user", "customer"} else "assistant"
        start_ms = item.get("start_ms")
        if not isinstance(start_ms, int) or start_ms < 0:
            start_ms = event_start_ms
        end_ms = item.get("end_ms")
        segment = TranscriptSegmentV1(
            id=("vapi-bootstrap-" if key.startswith("vapi:tool-bootstrap:") else "vapi-")
               + f"{hashlib.sha256(key.encode()).hexdigest()[:20]}-{index}",
            speaker=speaker, text=text,
            start_ms=start_ms if isinstance(start_ms, int) and start_ms >= 0 else None,
            end_ms=end_ms if isinstance(end_ms, int) and end_ms >= 0 else None,
            observed_at=(call_started_at + timedelta(milliseconds=start_ms)
                         if call_started_at is not None and isinstance(start_ms, int) and start_ms >= 0
                         else observed_at if message.get("type") != "end-of-call-report" else None),
        )
        for fact in deterministic_facts(segment) if speaker == "caller" else []:
            if fact.field == "narrative" and "narrative" in facts:
                continue
            if fact.field in {"location", "incident_time"} and fact.field in facts:
                continue
            facts[fact.field] = fact.model_dump(mode="json")
        return segment.model_dump(mode="json")

    if message.get("type") == "conversation-update":
        opening = _final_messages(message)
        if opening and not any(segment["speaker"] == "assistant" and segment["text"] == opening[0]["text"]
                               for segment in segments):
            segments.insert(0, add_segment(opening[0], 0))
    elif message.get("type") == "assistant.speechStarted":
        item = _final_messages(message)[0]
        call_id = call.get("id", "")
        segment_id = "vapi-speech-" + hashlib.sha256(
            f"{call_id}:{item['turn']}".encode()).hexdigest()[:20]
        previous = next((segment for segment in segments if segment["id"] == segment_id), None)
        if previous is None:
            segment = add_segment(item, 0)
            segment["id"] = segment_id
            segments.append(segment)
        elif previous["text"] != item["text"]:
            previous["text"] = item["text"]
    elif message.get("type") == "end-of-call-report":
        # Match each reported turn to at most one persisted final event. Two
        # identical sentences in different turns are still two real turns.
        ordered = []
        unused = list(segments)
        for index, item in enumerate(_final_messages(message)):
            speaker = "caller" if item["role"] in {"user", "customer"} else "assistant"
            match = next((segment for segment in unused if segment["speaker"] == speaker
                          and segment["text"] == item["text"]), None)
            if match is not None:
                ordered.append(match)
                unused.remove(match)
            else:
                # Vapi may combine several final transcript chunks into one
                # artifact message. Keep the original chunks and their source
                # IDs instead of showing the same caller speech twice.
                normalized = re.sub(r"\s+", " ", item["text"].strip())
                chunks = [segment for segment in unused if segment["speaker"] == speaker]
                joined = next((chunks[:count] for count in range(1, len(chunks) + 1)
                               if re.sub(r"\s+", " ", " ".join(
                                   segment["text"] for segment in chunks[:count]).strip()) == normalized), [])
                if joined:
                    ordered.extend(joined)
                    for segment in joined:
                        unused.remove(segment)
                else:
                    ordered.append(add_segment(item, index))
        # The final report sometimes omits the first assistant turn. Keep a
        # previously observed greeting ahead of the reported conversation.
        opening = None
        if unused and segments and unused[0] == segments[0] and unused[0]["speaker"] == "assistant":
            opening = unused.pop(0)
        segments = ([opening] if opening else []) + ordered + unused
        # A final report often repeats turns already stored by transcript
        # callbacks. Recover facts from those turns as well as new ones.
        for index, item in enumerate(segments):
            if item["speaker"] != "caller":
                continue
            segment = TranscriptSegmentV1.model_validate(item)
            if call_started_at is not None:
                segment = restore_segment_time(segment, call_started_at)
                segments[index] = segment.model_dump(mode="json")
            for fact in deterministic_facts(segment):
                previous = facts.get(fact.field)
                if previous is None or (previous["uncertainty"] == "uncertain" and fact.uncertainty == "explicit"):
                    facts[fact.field] = fact.model_dump(mode="json")
        artifact = message.get("artifact")
        if isinstance(artifact, dict):
            sourced, _ = _merge_sourced_facts(segments, list(facts.values()),
                                              _artifact_fact_candidates(artifact))
            facts = {fact["field"]: fact for fact in sourced}
    else:
        # A tool webhook can bootstrap the caller turn before Vapi's separate
        # final-transcript webhook completes. Match that same turn by its
        # timestamp; equal words in later turns must remain separate.
        for index, item in enumerate(_final_messages(message)):
            speaker = "caller" if item["role"] in {"user", "customer"} else "assistant"
            start_ms = item.get("start_ms")
            if not isinstance(start_ms, int) or start_ms < 0:
                start_ms = event_start_ms
            duplicate = (message.get("type") in {"transcript", 'transcript[transcriptType="final"]'} and
                         isinstance(start_ms, int) and any(
                             segment["speaker"] == speaker and segment["text"] == item["text"]
                             and segment["id"].startswith("vapi-bootstrap-")
                             and isinstance(segment.get("start_ms"), int)
                             and abs(segment["start_ms"] - start_ms) <= 1000
                             for segment in segments))
            if not duplicate:
                segments.append(add_segment(item, index))
    if all(segment.get("start_ms") is not None for segment in segments):
        segments.sort(key=lambda segment: segment["start_ms"])
    return segments, list(facts.values())


def _normalized_request(message: dict[str, Any], state: dict | None,
                        key: str) -> VoiceIntakeRequestV1:
    call_id, assistant_id, started_at = _call_context(message)
    if state is not None and state["call_started_at"] != started_at:
        raise HTTPException(409, detail={"code": "voice_event_conflict", "message": "Call start time changed.", "details": {}})
    event_type = "final_turn" if message["type"] in {"transcript", 'transcript[transcriptType="final"]',
                                                    "assistant.speechStarted", "conversation-update"} else "end_of_call_report"
    received_at = datetime.now(timezone.utc)
    raw_timestamp = message.get("timestamp")
    if isinstance(raw_timestamp, (int, float)):
        received_at = datetime.fromtimestamp(raw_timestamp / 1000, timezone.utc)
    elif isinstance(raw_timestamp, str):
        try:
            received_at = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    segments, facts = _segments_and_facts(state, message, key, received_at, started_at)
    call = message.get("call") if isinstance(message.get("call"), dict) else {}
    call_type = call.get("type")
    transport = "web" if call_type == "webCall" else "twilio"
    if state is not None:
        transport = state["telephony_provider"]
    return VoiceIntakeRequestV1.model_validate({
        "schema_version": 1, "provider": "vapi", "telephony_provider": transport,
        "speech_provider": "gradium", "provider_call_id": call_id,
        "sequence": state["sequence"] + 1 if state else 1,
        "source_event_key": key, "event_type": event_type,
        "call_started_at": started_at, "received_at": received_at,
        "assistant_id": assistant_id,
        "assistant_version": os.getenv("VOICE_ASSISTANT_VERSION", ""),
        "extractor_version": os.getenv("VOICE_EXTRACTOR_VERSION", "voice-rules-v3"),
        "segments": segments, "facts": facts, "mode": "live",
    })


def _initialize_from_partial(repository: VoiceIntakeRepository, actor_id,
                             message: dict[str, Any]) -> None:
    """Create an empty call session while the caller is still speaking."""
    call_id, assistant_id, started_at = _call_context(message)
    if repository.get_state(actor_id, call_id) is not None:
        return
    call = message["call"]
    request = VoiceIntakeRequestV1.model_validate({
        "schema_version": 1, "provider": "vapi",
        "telephony_provider": "web" if call.get("type") == "webCall" else "twilio",
        "speech_provider": "gradium", "provider_call_id": call_id,
        "sequence": 1, "source_event_key": "vapi:call-start:"
        + hashlib.sha256(call_id.encode("utf-8")).hexdigest(),
        "event_type": "call_started", "call_started_at": started_at,
        "received_at": started_at, "assistant_id": assistant_id,
        "assistant_version": os.getenv("VOICE_ASSISTANT_VERSION", ""),
        "extractor_version": os.getenv("VOICE_EXTRACTOR_VERSION", "voice-rules-v3"),
        "segments": [], "facts": [], "mode": "live",
    })
    _ingest(repository, actor_id, request)


@router.post("/server-events")
def server_event(payload: dict[str, Any], actor_id: VoiceOwner,
                 repository: VoiceRepositoryDependency, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Persist the full call after it ends; intermediate turns stay off the tool path."""
    message = _message(payload)
    event_type = message.get("type")
    if event_type in {"transcript", 'transcript[transcriptType="final"]',
                      "assistant.speechStarted", "conversation-update",
                      "end-of-call-report", "call.artifact.upload"}:
        _call_context(message)
    if event_type not in {"end-of-call-report", "call.artifact.upload"}:
        return {"ignored": True}
    if event_type == "call.artifact.upload":
        return {"ignored": True}
    call_id, _, _ = _call_context(message)
    key = _event_key(message)
    if repository.event_exists(actor_id, call_id, key):
        session = repository.get_session(actor_id, call_id)
        if event_type == "end-of-call-report":
            if session.telephony_provider == "twilio":
                background_tasks.add_task(_auto_fake_follow_up, actor_id, session.case_id)
            from claim_api.voice_recording import capture_original_recording
            background_tasks.add_task(capture_original_recording, repository, actor_id, call_id)
        return VoiceIngestResponse(session=session, replayed=True).model_dump(mode="json")
    for _ in range(3):
        state = repository.get_state(actor_id, call_id)
        if (event_type == "conversation-update" and state
                and state["transcript_json"] and state["transcript_json"][0]["speaker"] == "assistant"):
            return {"ignored": True}
        request = _normalized_request(message, state, key)
        response = _ingest(repository, actor_id, request)
        if not response.replayed or repository.event_exists(actor_id, call_id, key):
            if event_type == "end-of-call-report":
                if response.session.telephony_provider == "twilio":
                    background_tasks.add_task(_auto_fake_follow_up, actor_id, response.session.case_id)
                from claim_api.voice_recording import capture_original_recording
                background_tasks.add_task(capture_original_recording, repository, actor_id, call_id)
            return response.model_dump(mode="json")
    raise HTTPException(409, detail={"code": "voice_event_conflict", "message": "Concurrent voice event; retry.", "details": {}})


def bootstrap_tool_session(repository: VoiceIntakeRepository, actor_id,
                           message: dict[str, Any]) -> VoiceSessionView | None:
    """Persist Vapi's authenticated caller turn when its final webhook is still in flight."""
    artifact = message.get("artifact")
    items = artifact.get("messages") if isinstance(artifact, dict) else None
    if not isinstance(items, list):
        return None
    caller = next((item for item in reversed(items) if isinstance(item, dict)
                   and item.get("role") in {"user", "customer"}
                   and isinstance(item.get("message"), str)
                   and item["message"].strip()), None)
    if caller is None:
        return None
    timestamp = caller.get("time")
    if not isinstance(timestamp, (int, float)):
        return None
    transcript = {
        "type": "transcript", "role": caller["role"],
        "transcriptType": "final", "transcript": caller["message"],
        "timestamp": timestamp, "call": message.get("call"),
        "assistant": message.get("assistant"),
    }
    key = "vapi:tool-bootstrap:" + hashlib.sha256(json.dumps(
        (message.get("call", {}).get("id"), timestamp, caller["message"]),
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    call_id, _, _ = _call_context(transcript)
    for _ in range(3):
        state = repository.get_state(actor_id, call_id)
        if state is not None:
            spoken_ms = int(timestamp - state["call_started_at"].timestamp() * 1000)
            if any(segment["speaker"] == "caller" and segment["text"] == caller["message"]
                   and isinstance(segment.get("start_ms"), int)
                   and abs(segment["start_ms"] - spoken_ms) <= 1000
                   for segment in state["transcript_json"]):
                return repository._view(state)
        request = _normalized_request(transcript, state, key)
        response = _ingest(repository, actor_id, request)
        if not response.replayed or repository.event_exists(actor_id, call_id, key):
            return response.session
    return repository.get_session(actor_id, call_id)


def apply_tool_facts(repository: VoiceIntakeRepository, actor_id, call_id: str,
                     tool_call_id: str, parameters: dict[str, Any]) -> VoiceSessionView | None:
    """Use only persisted caller segments to source model-proposed tool facts."""
    state = repository.get_state(actor_id, call_id)
    if state is None:
        return None
    raw_candidates = parameters.get("facts", [])
    if not isinstance(raw_candidates, list):
        raise ValueError("Tool facts must be a list")
    if not raw_candidates:
        return repository._view(state)
    key = f"vapi:tool:{tool_call_id}"
    if repository.event_exists(actor_id, call_id, key):
        return repository._view(state)
    for attempt in range(3):
        if attempt:
            state = repository.get_state(actor_id, call_id)
        segments = [TranscriptSegmentV1.model_validate(item) for item in state["transcript_json"]]
        facts = {item["field"]: item for item in state["facts_json"]}
        accepted = 0
        with diagnostic_span("voice.tool.validate_facts", proposed_count=len(raw_candidates)) as span:
            for candidate in raw_candidates:
                try:
                    if not isinstance(candidate, dict):
                        raise ValueError("Tool fact must be an object")
                    excerpt = candidate.get("excerpt")
                    if not isinstance(excerpt, str) or not excerpt:
                        raise ValueError("Tool fact requires an excerpt")
                    source = next((segment for segment in reversed(segments)
                                   if segment.speaker == "caller" and excerpt in segment.text), None)
                    if source is None:
                        raise ValueError("Tool fact excerpt is absent from persisted caller transcript")
                    fact = VoiceFactV1.model_validate({
                        "field": candidate.get("field"), "value": candidate.get("value"),
                        "segment_id": source.id, "excerpt": excerpt,
                        "uncertainty": _candidate_uncertainty(candidate),
                    })
                    _assert_fact_supported(fact)
                    if fact.field == "location":
                        normalized = normalize_address(fact.value)
                        if normalized:
                            fact = fact.model_copy(update={"value": normalized})
                except ValueError:
                    continue
                facts[fact.field] = fact.model_dump(mode="json")
                accepted += 1
            if span is not None:
                span.set_attribute("accepted_count", accepted)
                span.set_attribute("rejected_count", len(raw_candidates) - accepted)
        if not accepted:
            # The persisted transcript still supplies deterministic facts. An
            # unsupported model proposal must not turn a valid call into a
            # technical failure or trigger another Vapi tool attempt.
            return repository._view(state)
        if list(facts.values()) == state["facts_json"]:
            return repository._view(state)
        request = VoiceIntakeRequestV1.model_validate({
            "schema_version": 1, "provider_call_id": call_id, "mode": "live",
            "telephony_provider": state["telephony_provider"],
            "sequence": state["sequence"] + 1, "source_event_key": key,
            "event_type": "final_turn", "call_started_at": state["call_started_at"],
            "received_at": datetime.now(timezone.utc),
            "assistant_id": state["assistant_id"],
            "assistant_version": state["assistant_version"],
            "extractor_version": os.getenv("VOICE_EXTRACTOR_VERSION", "voice-rules-v3"),
            "segments": segments, "facts": list(facts.values()),
        })
        response = _ingest(repository, actor_id, request)
        if not response.replayed or repository.event_exists(actor_id, call_id, key):
            return response.session
    raise ValueError("Concurrent tool call; retry")
