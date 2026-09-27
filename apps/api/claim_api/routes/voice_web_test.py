"""Owner-only, short-lived Vapi access for browser voice demonstrations."""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from claim_api.auth import CurrentUser
from claim_api.routes.voice import get_voice_repository
from claim_api.voice_repository import VoiceIntakeRepository


router = APIRouter(prefix="/v1/voice/web-test", tags=["voice-test"])
Repository = Annotated[VoiceIntakeRepository, Depends(get_voice_repository)]


def _owner(user: CurrentUser) -> UUID:
    configured = os.getenv("VOICE_OWNER_USER_ID", "")
    if not configured:
        raise HTTPException(503, detail={"code": "voice_test_unconfigured", "message": "Voice test owner is not configured."})
    try:
        owner = UUID(configured)
        actor = UUID(user.id)
    except ValueError as error:
        raise HTTPException(503, detail={"code": "voice_test_unconfigured", "message": "Voice test owner is invalid."}) from error
    if not hmac.compare_digest(str(actor), str(owner)):
        raise HTTPException(403, detail={"code": "voice_test_forbidden", "message": "Voice test is limited to the demo handler."})
    return actor


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


@router.post("/session")
def web_test_session(request: Request, response: Response, user: CurrentUser) -> dict[str, str]:
    _owner(user)
    if os.getenv("VOICE_WEB_TEST_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(503, detail={"code": "voice_test_disabled", "message": "Browser voice test is disabled."})
    origin = request.headers.get("origin", "")
    allowed = {part.strip().rstrip("/") for part in os.getenv("CORS_ORIGINS", "").split(",") if part.strip()}
    if not origin or origin not in allowed or not origin.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
        raise HTTPException(403, detail={"code": "voice_test_origin_forbidden", "message": "Origin is not allowed."})
    key = os.getenv("VAPI_PRIVATE_API_KEY", "")
    org_id = os.getenv("VAPI_ORG_ID", "")
    assistant_id = os.getenv("VOICE_ASSISTANT_ID", "")
    if not all((key, org_id, assistant_id)):
        raise HTTPException(503, detail={"code": "voice_test_unconfigured", "message": "Vapi web test is not configured."})
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=5)
    token = jwt.encode({
        "orgId": org_id,
        "token": {"tag": "public", "restrictions": {
            "enabled": True, "allowedOrigins": [origin],
            "allowedAssistantIds": [assistant_id], "allowTransientAssistant": False,
        }},
        "iat": int(now.timestamp()), "exp": int(expires.timestamp()),
    }, key, algorithm="HS256")
    _no_store(response)
    return {"token": token, "assistant_id": assistant_id, "expires_at": expires.isoformat()}


@router.get("/calls/{call_id}")
def web_test_call(call_id: str, response: Response, user: CurrentUser,
                  repository: Repository) -> dict[str, str]:
    actor = _owner(user)
    if not call_id or len(call_id) > 200 or not all(char.isalnum() or char in "-_" for char in call_id):
        raise HTTPException(404, detail={"code": "voice_test_call_not_found", "message": "Browser call not found."})
    row = repository.get_state(actor, call_id)
    if row is None or row["telephony_provider"] != "web":
        raise HTTPException(404, detail={"code": "voice_test_call_not_found", "message": "Browser call not found."})
    _no_store(response)
    return {"case_id": str(row["case_id"]), "status": row["triage_json"]["status"],
            "recording_status": row["recording_status"]}
