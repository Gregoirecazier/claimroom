"""Guest chatbot: Pipelex drafts a turn, the insured confirms writes separately."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from claim_api.agent_tracing import agent_span
from claim_api.insured_portal import CORRECTABLE, InsuredPortalService


METHOD_PATH = Path(__file__).parent / "methods" / "deposit_chat_v1.mthds"


class ChatUnavailable(RuntimeError):
    pass


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["insured", "assistant"]
    text: str = Field(min_length=1, max_length=1000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)
    target_field: str | None = Field(default=None, max_length=64)

    @field_validator("message")
    @classmethod
    def nonblank_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty")
        return value

    @field_validator("target_field")
    @classmethod
    def supported_target(cls, value: str | None) -> str | None:
        if value is not None and value not in CORRECTABLE:
            raise ValueError("Unsupported target field")
        return value


class ChatProposal(BaseModel):
    field: str
    value: str


class ChatResponse(BaseModel):
    reply: str = Field(min_length=1, max_length=1200)
    proposal: ChatProposal | None = None


async def _execute(context_json: str) -> dict:
    from claim_api.analysis import _PIPELEX_BOOT_LOCK, _ensure_pipelex_home_is_writable
    from pipelex.pipelex import Pipelex
    from pipelex.pipeline.runner import PipelexMTHDSProtocol

    _ensure_pipelex_home_is_writable()
    with _PIPELEX_BOOT_LOCK:
        if not Pipelex.is_fully_booted():
            Pipelex.make()
    result = await PipelexMTHDSProtocol().execute(
        mthds_contents=[METHOD_PATH.read_text(encoding="utf-8")],
        inputs={"context_json": context_json},
    )
    content = result.pipe_output.main_stuff.content
    if hasattr(content, "smart_dump"):
        return content.smart_dump()
    if isinstance(content, str):
        return json.loads(content)
    if isinstance(content, dict):
        return content
    raise ValueError("Invalid chat output")


def _proposal(field: str, value: str) -> ChatProposal | None:
    field, value = field.strip(), value.strip()
    if field not in CORRECTABLE or not value or len(value) > 1000:
        return None
    if field in {"danger_status", "injury_status"} and value not in {"yes", "no", "unknown"}:
        return None
    if field == "incident_at":
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        value = parsed.isoformat()
    return ChatProposal(field=field, value=value)


def reply(service: InsuredPortalService, session_token: str, request: ChatRequest) -> ChatResponse:
    # Authorize before transmitting any case text to the model.
    service.authorize(session_token, "correct_intake")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise ChatUnavailable("Chat assistant is not configured")
    summary = service.summary(session_token)
    context = {
        "case": {"intake": summary["intake"], "missing_fields": summary["missing_fields"],
                 "analysis_requests": summary.get("analysis_requests", [])},
        "target_field": request.target_field,
        "conversation": [*([message.model_dump() for message in request.history]),
                         {"role": "insured", "text": request.message}],
    }
    with agent_span("deposit.chat", agent_name="Claimroom insured assistant",
                    version="deposit-chat-v1", provider="pipelex"):
        output = asyncio.run(asyncio.wait_for(
            _execute(json.dumps(context, ensure_ascii=False, default=str)), timeout=20))
    if not isinstance(output, dict):
        raise ValueError("Invalid chat output")
    raw_answer = output.get("reply")
    if not isinstance(raw_answer, str):
        raise ValueError("Invalid chat reply")
    answer = raw_answer.strip()
    if not answer or len(answer) > 1200:
        raise ValueError("Invalid chat reply")
    field, value = output.get("proposal_field"), output.get("proposal_value")
    proposal = _proposal(field, value) if isinstance(field, str) and isinstance(value, str) else None
    if request.target_field and proposal and proposal.field != request.target_field:
        proposal = None
    return ChatResponse(reply=answer, proposal=proposal)
