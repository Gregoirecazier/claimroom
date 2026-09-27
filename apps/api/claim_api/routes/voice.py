"""Synthetic voice intake and explicitly enabled Vapi server boundary."""

from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from claim_api.auth import CurrentUser
from claim_api.agent_tracing import diagnostic_span
from claim_api.postgres_cases import DatabaseConfigurationError, DatabaseUnavailableError
from claim_api.voice_intake import VoiceIngestResponse, VoiceIntakeRequestV1, VoiceSessionView
from claim_api.voice_repository import VoiceEventConflict, VoiceIntakeRepository
from claim_api.storage import SupabaseStorageAdapter, StorageConfigurationError, StorageAdapterError, StorageUnavailableError


router = APIRouter(prefix="/v1/voice", tags=["voice-intake"])


def get_voice_repository() -> VoiceIntakeRepository:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(503, detail={"code": "database_not_configured", "message": "DATABASE_URL is required.", "details": {}})
    try:
        return VoiceIntakeRepository(database_url)
    except DatabaseConfigurationError as error:
        raise HTTPException(503, detail={"code": "database_tls_required", "message": str(error), "details": {}}) from error


VoiceRepositoryDependency = Annotated[VoiceIntakeRepository, Depends(get_voice_repository)]


@router.post("/{case_id}/reextract", response_model=VoiceIngestResponse)
def reextract_voice_call(case_id: UUID, user: CurrentUser,
                         repository: VoiceRepositoryDependency) -> VoiceIngestResponse:
    try:
        actor_id = UUID(user.id)
    except ValueError as error:
        raise HTTPException(403, detail={"code": "invalid_actor", "message": "A Supabase user UUID is required.", "details": {}}) from error
    try:
        result = repository.reextract(actor_id, case_id)
    except VoiceEventConflict as error:
        raise HTTPException(409, detail={"code": "voice_event_conflict", "message": str(error), "details": {}}) from error
    except DatabaseUnavailableError as error:
        raise HTTPException(503, detail={"code": "database_unavailable", "message": "Voice storage unavailable.", "details": {}}) from error
    if result is None:
        raise HTTPException(404, detail={"code": "voice_session_not_found", "message": "Voice session not found.", "details": {}})
    return result


@router.get("/{case_id}/recording/read-url")
def voice_recording_read_url(case_id: UUID, user: CurrentUser,
                             repository: VoiceRepositoryDependency) -> dict:
    try:
        actor_id = UUID(user.id)
    except ValueError as error:
        raise HTTPException(403, detail={"code": "invalid_actor", "message": "A Supabase user UUID is required.", "details": {}}) from error
    row = repository.get_case_recording(actor_id, case_id)
    if row is None:
        raise HTTPException(404, detail={"code": "voice_recording_not_found", "message": "Recording not found.", "details": {}})
    if row["recording_status"] != "available" or not row["recording_storage_path"]:
        raise HTTPException(409, detail={"code": "voice_recording_unavailable", "message": "Recording is not available.", "details": {}})
    try:
        signed_url = SupabaseStorageAdapter.from_env(
            bucket=os.getenv("VOICE_RECORDING_BUCKET", "claim-voice-recordings")
        ).create_signed_read_url(row["recording_storage_path"], 300)
    except (StorageConfigurationError, StorageAdapterError, StorageUnavailableError) as error:
        raise HTTPException(503, detail={"code": "recording_storage_unavailable", "message": "Private recording storage unavailable.", "details": {}}) from error
    return {"url": signed_url, "expires_in": 300, "mime_type": row["recording_mime_type"]}


@router.post("/{case_id}/recording/retry")
def retry_voice_recording(case_id: UUID, user: CurrentUser,
                          repository: VoiceRepositoryDependency) -> dict:
    try:
        actor_id = UUID(user.id)
    except ValueError as error:
        raise HTTPException(403, detail={"code": "invalid_actor", "message": "A Supabase user UUID is required.", "details": {}}) from error
    row = repository.get_case_recording(actor_id, case_id)
    if row is None:
        raise HTTPException(404, detail={"code": "voice_recording_not_found", "message": "Recording not found.", "details": {}})
    from claim_api.voice_recording import capture_original_recording
    capture_original_recording(repository, actor_id, row["provider_call_id"])
    current = repository.get_case_recording(actor_id, case_id)
    return {"status": current["recording_status"], "error_code": current["recording_error_code"]}


def _ingest(repository: VoiceIntakeRepository, actor_id: UUID,
            request: VoiceIntakeRequestV1) -> VoiceIngestResponse:
    try:
        return repository.ingest(actor_id, request)
    except VoiceEventConflict as error:
        raise HTTPException(409, detail={"code": "voice_event_conflict", "message": str(error), "details": {}}) from error
    except (ValueError, DatabaseUnavailableError) as error:
        if isinstance(error, DatabaseUnavailableError):
            raise HTTPException(503, detail={"code": "database_unavailable", "message": "Voice storage unavailable.", "details": {}}) from error
        raise HTTPException(422, detail={"code": "invalid_voice_intake", "message": str(error), "details": {}}) from error


def _auto_fake_follow_up(actor_id: UUID, case_id: UUID) -> None:
    if os.getenv("SMS_LINK_DELIVERY_MODE", "disabled").strip().lower() == "twilio":
        from claim_api.routes.sms_link import get_sms_link_service
        try:
            get_sms_link_service().auto_follow_up(actor_id, case_id)
        except Exception as error:
            logging.getLogger(__name__).warning("Automatic deposit SMS pending: %s", getattr(error,"code",type(error).__name__))
        return
    if os.getenv("WHATSAPP_DELIVERY_MODE", "mock").strip().lower() != "fake_whatsapp":
        return
    from claim_api.routes.sms import get_message_service
    try:
        get_message_service().auto_fake_follow_up(actor_id, case_id)
    except Exception as error:
        # Voice intake has already been persisted. Opening the case retries
        # this idempotent fake delivery if Vapi's caller ID is not ready yet.
        logging.getLogger(__name__).warning("Automatic fake WhatsApp follow-up failed: %s",
                                            getattr(error, "code", type(error).__name__))


@router.post("/mock/intake-sessions", response_model=VoiceIngestResponse)
def mock_intake(request: VoiceIntakeRequestV1, user: CurrentUser,
                repository: VoiceRepositoryDependency) -> VoiceIngestResponse:
    if request.mode != "mock":
        raise HTTPException(422, detail={"code": "mock_only", "message": "This route accepts only mock voice sessions.", "details": {}})
    try:
        actor_id = UUID(user.id)
    except ValueError as error:
        raise HTTPException(403, detail={"code": "invalid_actor", "message": "A Supabase user UUID is required.", "details": {}}) from error
    return _ingest(repository, actor_id, request)


def _live_owner(authorization: Annotated[str | None, Header()] = None,
                x_vapi_secret: Annotated[str | None, Header(alias="X-Vapi-Secret")] = None) -> UUID:
    if os.getenv("VOICE_LIVE_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(503, detail={"code": "voice_live_disabled", "message": "Live voice intake is disabled.", "details": {}})
    secret = os.getenv("VOICE_WEBHOOK_SECRET", "")
    owner = os.getenv("VOICE_OWNER_USER_ID", "")
    if not secret or not owner:
        raise HTTPException(503, detail={"code": "voice_live_unconfigured", "message": "Live voice intake is not configured.", "details": {}})
    token = x_vapi_secret or (authorization or "").removeprefix("Bearer ")
    if not hmac.compare_digest(token, secret):
        raise HTTPException(401, detail={"code": "invalid_voice_credential", "message": "Invalid voice server credential.", "details": {}})
    if not os.getenv("VOICE_ASSISTANT_ID", "") or not os.getenv("VOICE_ASSISTANT_VERSION", ""):
        raise HTTPException(503, detail={"code": "voice_live_unconfigured", "message": "Assistant identity not configured.", "details": {}})
    try:
        return UUID(owner)
    except ValueError as error:
        raise HTTPException(503, detail={"code": "voice_live_unconfigured", "message": "Invalid voice owner configuration.", "details": {}}) from error


VoiceOwner = Annotated[UUID, Depends(_live_owner)]


@router.post("/vapi/normalized-events", response_model=VoiceIngestResponse)
def vapi_event(request: VoiceIntakeRequestV1, actor_id: VoiceOwner,
               repository: VoiceRepositoryDependency) -> VoiceIngestResponse:
    if request.mode != "live":
        raise HTTPException(422, detail={"code": "live_only", "message": "Vapi events require live mode.", "details": {}})
    expected_assistant = os.getenv("VOICE_ASSISTANT_VERSION", "")
    expected_assistant_id = os.getenv("VOICE_ASSISTANT_ID", "")
    if (not expected_assistant or not expected_assistant_id
            or request.assistant_version != expected_assistant
            or request.assistant_id != expected_assistant_id):
        raise HTTPException(403, detail={"code": "voice_assistant_mismatch", "message": "Unexpected assistant version.", "details": {}})
    result = _ingest(repository, actor_id, request)
    if request.event_type == "end_of_call_report" and request.telephony_provider == "twilio":
        _auto_fake_follow_up(actor_id, result.session.case_id)
    return result


class VapiCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=200)
    assistantId: str | None = None
    createdAt: str | None = None
    startedAt: str | None = None
    type: str | None = None


class VapiToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=200)
    name: str | None = None
    parameters: dict = Field(default_factory=dict)
    function: dict | None = None


class VapiToolMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["tool-calls"]
    call: VapiCall
    toolCallList: list[VapiToolCall] = Field(min_length=1)
    artifact: dict | None = None


class VapiToolWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: VapiToolMessage


class NextIntakeStepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ask", "complete", "urgent_human_handoff", "incomplete", "error"]
    next_question: str | None
    missing_fields: list[str]
    case_id: UUID | None


@router.post("/vapi/next-intake-step")
def next_intake_step(request: VapiToolWebhook, actor_id: VoiceOwner,
                     repository: VoiceRepositoryDependency) -> dict:
    """Native Vapi function-tool response; never trust model parameters for call ID."""
    with _voice_agent_span():
        return _next_intake_step(request, actor_id, repository)


def _voice_agent_span():
    """Expose a Vapi tool turn as an agent run without claim text or identifiers."""
    from claim_api.agent_tracing import agent_span

    return agent_span(
        "voice.intake",
        agent_name="Claimroom voice intake",
        version=os.getenv("VOICE_ASSISTANT_VERSION", "unknown"),
        provider="vapi",
    )


def _next_intake_step(request: VapiToolWebhook, actor_id: UUID,
                      repository: VoiceIntakeRepository) -> dict:
    assistant_id = request.message.call.assistantId
    if not assistant_id or assistant_id != os.getenv("VOICE_ASSISTANT_ID", ""):
        raise HTTPException(403, detail={"code": "voice_assistant_mismatch", "message": "Unexpected assistant.", "details": {}})
    call_hash = hashlib.sha256(request.message.call.id.encode()).hexdigest()[:16]
    cached = isinstance(request.message.artifact, dict) and isinstance(
        request.message.artifact.get("messages"), list)
    session: VoiceSessionView | None = None
    if not cached:
        try:
            with diagnostic_span("voice.tool.load_turn", call_hash=call_hash):
                session = repository.get_session(actor_id, request.message.call.id)
        except DatabaseUnavailableError as error:
            raise HTTPException(503, detail={"code": "database_unavailable", "message": "Voice storage unavailable.", "details": {}}) from error
    results = []
    for tool_call in request.message.toolCallList:
        function = tool_call.function if isinstance(tool_call.function, dict) else {}
        name = function.get("name") or tool_call.name
        arguments = function.get("arguments", tool_call.parameters)
        if not isinstance(arguments, dict):
            arguments = {}
        if name != "next_intake_step":
            results.append({"name": name, "toolCallId": tool_call.id,
                            "error": "unsupported_tool"})
        elif cached:
            from claim_api.vapi_native import cached_tool_turn
            with diagnostic_span("voice.tool.cached_decision", call_hash=call_hash):
                _, triage = cached_tool_turn(
                    request.message.model_dump(exclude_none=True), arguments)
            decision = NextIntakeStepResponse(
                decision="ask" if triage.status == "collecting" else triage.status,
                next_question=triage.next_question, missing_fields=triage.missing_p0,
                case_id=None,
            )
            results.append({"name": name, "toolCallId": tool_call.id,
                            "result": json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)})
        elif session is None:
            results.append({"name": name, "toolCallId": tool_call.id,
                            "error": "voice_session_not_found"})
        else:
            if arguments.get("facts"):
                from claim_api.vapi_native import apply_tool_facts
                try:
                    with diagnostic_span("voice.tool.apply_facts", call_hash=call_hash,
                                         proposed_count=len(arguments["facts"])
                                         if isinstance(arguments["facts"], list) else 0):
                        session = apply_tool_facts(repository, actor_id, request.message.call.id,
                                                   tool_call.id, arguments)
                except (ValueError, HTTPException):
                    results.append({"name": name, "toolCallId": tool_call.id,
                                    "error": "invalid_sourced_facts"})
                    continue
            triage = session.triage
            decision = NextIntakeStepResponse(
                decision="ask" if triage.status == "collecting" else triage.status,
                next_question=triage.next_question, missing_fields=triage.missing_p0,
                case_id=session.case_id,
            )
            results.append({"name": name, "toolCallId": tool_call.id,
                            "result": json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)})
    return {"results": results}
