"""Transactional voice intake persistence and idempotency."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from psycopg.types.json import Jsonb

from claim_api.agent_tracing import diagnostic_span, trace_operation
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.voice_intake import (
    TranscriptSegmentV1, VoiceIngestResponse, VoiceIntakeRequestV1, VoiceSessionView,
    VoiceTriageV1, deterministic_facts, process_voice_intake, restore_segment_time,
)


class VoiceEventConflict(ValueError):
    pass


class VoiceIntakeRepository(PostgresCaseRepository):
    def reextract(self, actor_id: UUID, case_id: UUID) -> VoiceIngestResponse | None:
        """Revisit an incomplete call using only its persisted caller turns."""
        row = self.get_case_recording(actor_id, case_id)
        if row is None:
            return None
        if row["triage_json"]["status"] != "incomplete":
            raise VoiceEventConflict("Only an incomplete call can be re-extracted")
        key = "voice:reextract:rules-v3"
        if self.event_exists(actor_id, row["provider_call_id"], key):
            current = self.get_case_recording(actor_id, case_id)
            return VoiceIngestResponse(session=self._view(current), replayed=True)
        facts = {item["field"]: item for item in row["facts_json"]}
        segments = []
        for item in row["transcript_json"]:
            segment = restore_segment_time(
                TranscriptSegmentV1.model_validate(item), row["call_started_at"])
            segments.append(segment.model_dump(mode="json"))
            if segment.speaker != "caller":
                continue
            for fact in deterministic_facts(segment):
                previous = facts.get(fact.field)
                if previous is None or (previous["uncertainty"] == "uncertain" and fact.uncertainty == "explicit"):
                    facts[fact.field] = fact.model_dump(mode="json")
        request = VoiceIntakeRequestV1.model_validate({
            "schema_version": 1, "provider": "vapi",
            "provider_call_id": row["provider_call_id"],
            "telephony_provider": row["telephony_provider"],
            "speech_provider": row["speech_provider"],
            "sequence": row["sequence"] + 1, "source_event_key": key,
            "event_type": "end_of_call_report",
            "call_started_at": row["call_started_at"],
            "received_at": datetime.now(timezone.utc),
            "assistant_id": row["assistant_id"],
            "assistant_version": row["assistant_version"],
            "extractor_version": "voice-rules-v3",
            "segments": segments, "facts": list(facts.values()),
            "mode": row["mode"],
        })
        intake, triage = process_voice_intake(request)
        if (list(facts.values()) == row["facts_json"] and segments == row["transcript_json"]
                and triage.model_dump(mode="json") == row["triage_json"]
                and intake.model_dump(mode="json") == row["projected_intake_json"]):
            return VoiceIngestResponse(session=self._view(row), replayed=True)
        return self.ingest(actor_id, request)

    def update_recording(self, actor_id: UUID, provider_call_id: str, *, status: str,
                         storage_path: str | None = None, mime_type: str | None = None,
                         byte_size: int | None = None, sha256: str | None = None,
                         error_code: str | None = None) -> dict:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """select s.*, c.created_by_user_id from public.voice_sessions s
                   join public.cases c on c.id = s.case_id
                   where s.provider = 'vapi' and s.provider_call_id = %s for update of s""",
                (provider_call_id,),
            )
            row = cursor.fetchone()
            if row is None or row["created_by_user_id"] != actor_id:
                raise VoiceEventConflict("Voice session not found")
            if row["recording_status"] == "available":
                if status != "available" or row["recording_sha256"] != sha256:
                    return row
                return row
            if (row["recording_status"] == status and row["recording_storage_path"] == storage_path
                    and row["recording_error_code"] == error_code):
                return row
            cursor.execute(
                """update public.voice_sessions set recording_status = %s,
                   recording_storage_path = %s, recording_mime_type = %s,
                   recording_byte_size = %s, recording_sha256 = %s,
                   recording_error_code = %s, updated_at = now()
                   where id = %s returning *""",
                (status, storage_path, mime_type, byte_size, sha256, error_code, row["id"]),
            )
            return cursor.fetchone()

    def get_case_recording(self, actor_id: UUID, case_id: UUID) -> dict | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """select s.* from public.voice_sessions s join public.cases c on c.id = s.case_id
                   where s.case_id = %s and c.created_by_user_id = %s""",
                (case_id, actor_id),
            )
            return cursor.fetchone()

    @trace_operation("voice.db.read_state")
    def get_state(self, actor_id: UUID, provider_call_id: str) -> dict | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """select s.* from public.voice_sessions s
                   join public.cases c on c.id = s.case_id
                   where s.provider = 'vapi' and s.provider_call_id = %s
                     and c.created_by_user_id = %s""",
                (provider_call_id, actor_id),
            )
            return cursor.fetchone()

    @trace_operation("voice.db.event_exists")
    def event_exists(self, actor_id: UUID, provider_call_id: str, source_event_key: str) -> bool:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """select 1 from public.voice_session_events e
                   join public.voice_sessions s on s.id = e.session_id
                   join public.cases c on c.id = s.case_id
                   where s.provider = 'vapi' and s.provider_call_id = %s
                     and c.created_by_user_id = %s and e.source_event_key = %s""",
                (provider_call_id, actor_id, source_event_key),
            )
            return cursor.fetchone() is not None

    def get_session(self, actor_id: UUID, provider_call_id: str) -> VoiceSessionView | None:
        row = self.get_state(actor_id, provider_call_id)
        return self._view(row) if row else None

    @trace_operation("voice.db.ingest")
    def ingest(self, actor_id: UUID, request: VoiceIntakeRequestV1) -> VoiceIngestResponse:
        canonical = request.model_dump(mode="json")
        payload_hash = hashlib.sha256(json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

        with self._connection() as connection, connection.cursor() as cursor:
            # Serializes creation before the unique session row exists as well as
            # updates that arrive concurrently from Vapi retries/webhooks.
            with diagnostic_span("voice.db.acquire_lock"):
                cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                               (f"voice:vapi:{request.provider_call_id}",))
            cursor.execute(
                """select s.*, c.created_by_user_id, c.state_version, c.content_revision,
                          c.intake_json, c.status as case_status
                   from public.voice_sessions s join public.cases c on c.id = s.case_id
                   where s.provider = %s and s.provider_call_id = %s for update of s, c""",
                (request.provider, request.provider_call_id),
            )
            prior = cursor.fetchone()
            if prior is not None and prior["created_by_user_id"] != actor_id:
                # Do not disclose that a call ID belongs to another handler.
                raise VoiceEventConflict("Voice session cannot be reassigned")
            if prior is not None and prior["mode"] != request.mode:
                raise VoiceEventConflict("Voice session mode cannot change")
            if prior is not None and prior["telephony_provider"] != request.telephony_provider:
                raise VoiceEventConflict("Voice session transport cannot change")
            if prior is not None and (prior["assistant_id"] != request.assistant_id
                                      or prior["call_started_at"] != request.call_started_at):
                raise VoiceEventConflict("Voice session identity or start time cannot change")

            if prior is not None:
                cursor.execute(
                    """select payload_sha256 from public.voice_session_events
                       where session_id = %s and source_event_key = %s""",
                    (prior["id"], request.source_event_key),
                )
                event = cursor.fetchone()
                if event is not None:
                    if event["payload_sha256"] != payload_hash:
                        raise VoiceEventConflict("The same event key has different content")
                    return VoiceIngestResponse(session=self._view(prior), replayed=True)
                if request.sequence <= prior["sequence"]:
                    # Old, previously unseen callbacks cannot roll back a newer
                    # transcript. A retried known key was handled above.
                    return VoiceIngestResponse(session=self._view(prior), replayed=True)
                if prior["intake_json"] != prior["projected_intake_json"]:
                    raise VoiceEventConflict("Handler-edited intake requires human reconciliation")

            effective = request
            if prior is not None and not request.segments and request.event_type != "final_turn":
                effective = VoiceIntakeRequestV1.model_validate({
                    **request.model_dump(mode="json"),
                    "segments": request.segments or prior["transcript_json"],
                    "facts": request.facts or prior["facts_json"],
                })
            intake, triage = process_voice_intake(effective)
            segments_json = [item.model_dump(mode="json") for item in effective.segments]
            facts_json = [item.model_dump(mode="json") for item in effective.facts]
            triage_json = triage.model_dump(mode="json")
            intake_json = intake.model_dump(mode="json")

            if prior is None:
                cursor.execute(
                    """insert into public.cases
                       (created_by_user_id, scenario_id, synthetic, status, state_version,
                        content_revision, intake_json)
                       values (%s, %s, true, 'collecting', 1, 1, %s) returning id""",
                    (actor_id, "voice_mock" if request.mode == "mock" else
                     "voice_web" if request.telephony_provider == "web" else "voice_live", Jsonb(intake_json)),
                )
                case_id = cursor.fetchone()["id"]
                cursor.execute(
                    """insert into public.voice_sessions
                       (case_id, provider, provider_call_id, mode, telephony_provider,
                       speech_provider, call_started_at, assistant_id, assistant_version,
                        extractor_version, sequence, triage_json, projected_intake_json,
                        transcript_json, facts_json, recording_status)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       returning id""",
                    (case_id, request.provider, request.provider_call_id, request.mode,
                     request.telephony_provider, request.speech_provider,
                     request.call_started_at, request.assistant_id, request.assistant_version,
                     request.extractor_version, request.sequence,
                     Jsonb(triage_json), Jsonb(intake_json), Jsonb(segments_json), Jsonb(facts_json),
                     "unavailable" if request.mode == "mock" else "pending"),
                )
                session_id = cursor.fetchone()["id"]
                before_state = before_content = 0
                after_state = after_content = 1
                material = True
            else:
                case_id = prior["case_id"]
                session_id = prior["id"]
                before_state, before_content = prior["state_version"], prior["content_revision"]
                material = (
                    prior["intake_json"] != intake_json
                    or prior["triage_json"] != triage_json
                    or prior["facts_json"] != facts_json
                    or prior["transcript_json"] != segments_json
                )
                if material:
                    cursor.execute(
                        """update public.cases set intake_json = %s,
                           state_version = state_version + 1,
                           content_revision = content_revision + 1,
                           status = 'collecting', current_draft_id = null,
                           updated_at = now() where id = %s""",
                        (Jsonb(intake_json), case_id),
                    )
                    cursor.execute(
                        """update public.approvals set superseded_at = now()
                           where case_id = %s and superseded_at is null""",
                        (case_id,),
                    )
                cursor.execute(
                    """update public.voice_sessions set sequence = %s, triage_json = %s,
                       projected_intake_json = %s,
                       transcript_json = %s, facts_json = %s,
                       assistant_id = %s, assistant_version = %s, extractor_version = %s, updated_at = now()
                       where id = %s""",
                    (request.sequence, Jsonb(triage_json), Jsonb(intake_json), Jsonb(segments_json), Jsonb(facts_json),
                     request.assistant_id, request.assistant_version, request.extractor_version, session_id),
                )
                after_state = before_state + int(material)
                after_content = before_content + int(material)

            cursor.execute(
                """insert into public.voice_session_events
                   (session_id, source_event_key, sequence, event_type, payload_sha256,
                    transcript_json, facts_json, triage_json, received_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (session_id, request.source_event_key, request.sequence, request.event_type,
                 payload_hash, Jsonb(segments_json), Jsonb(facts_json), Jsonb(triage_json), request.received_at),
            )
            if material:
                cursor.execute(
                    """insert into public.audit_events
                       (case_id, actor_user_id, event_type, state_version_before,
                        state_version_after, content_revision_before,
                        content_revision_after, metadata_json)
                       values (%s, %s, 'case.voice_intake_recorded', %s, %s, %s, %s, %s)""",
                    (case_id, actor_id, before_state, after_state, before_content, after_content,
                     Jsonb({"provider": request.provider, "session_id": request.provider_call_id,
                            "event_key": request.source_event_key, "sequence": request.sequence,
                            "triage_status": triage.status, "mode": request.mode,
                            "assistant_version": request.assistant_version,
                            "assistant_id": request.assistant_id,
                            "extractor_version": request.extractor_version,
                            "payload_sha256": payload_hash})),
                )
            return VoiceIngestResponse(session=VoiceSessionView(
                case_id=case_id, provider=request.provider,
                provider_call_id=request.provider_call_id, sequence=request.sequence,
                mode=request.mode, telephony_provider=request.telephony_provider,
                triage=triage, facts=effective.facts,
                call_started_at=request.call_started_at,
                assistant_id=request.assistant_id,
                assistant_version=request.assistant_version,
                extractor_version=request.extractor_version,
            ), replayed=False)

    @staticmethod
    def _view(row: dict) -> VoiceSessionView:
        return VoiceSessionView(
            case_id=row["case_id"], provider=row["provider"],
            provider_call_id=row["provider_call_id"], sequence=row["sequence"],
            mode=row["mode"], telephony_provider=row["telephony_provider"],
            triage=VoiceTriageV1.model_validate(row["triage_json"]),
            facts=row["facts_json"], call_started_at=row["call_started_at"],
            assistant_id=row["assistant_id"],
            assistant_version=row["assistant_version"], extractor_version=row["extractor_version"],
        )
