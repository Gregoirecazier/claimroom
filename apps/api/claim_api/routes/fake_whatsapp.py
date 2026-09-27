"""Browser fake phone, authorized by the sent message's private deposit session."""

from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from claim_api.deposit_grants import DepositGrantError
from claim_api.fake_whatsapp import FakeWhatsAppService
from claim_api.postgres_cases import DatabaseUnavailableError
from claim_api.routes.insured_portal import bearer
from claim_api.sms_service import SmsError


router = APIRouter(prefix="/v1/fake-whatsapp", tags=["fake WhatsApp phone"])


class FakeReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_message_id: UUID
    body: str = Field(default="", max_length=1600)
    evidence_id: UUID | None = None


def service() -> FakeWhatsAppService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(503, "Fake WhatsApp storage is unavailable")
    return FakeWhatsAppService(database_url)


Session = Annotated[str, Depends(bearer)]
Service = Annotated[FakeWhatsAppService, Depends(service)]


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, DepositGrantError):
        status = {"invalid_session": 401, "expired_session": 401, "expired_grant": 410,
                  "revoked_grant": 410, "forbidden_capability": 403}.get(error.code, 401)
        return HTTPException(status, detail={"code": error.code, "message": "Fake phone session is unavailable.", "details": {}})
    if isinstance(error, SmsError):
        return HTTPException(error.status_code, detail={"code": error.code, "message": str(error), "details": {}})
    if isinstance(error, DatabaseUnavailableError):
        return HTTPException(503, "Fake WhatsApp storage is unavailable")
    raise error


@router.get("/conversation")
def conversation(token: Session, fake: Service, response: Response) -> dict:
    try:
        result = fake.get(token)
    except Exception as error:
        raise _translate(error) from error
    _private(response)
    return result


@router.post("/messages", status_code=201)
def send_reply(request: FakeReply, token: Session, fake: Service, response: Response) -> dict:
    try:
        result = fake.reply(token, client_message_id=request.client_message_id,
                            body=request.body, evidence_id=request.evidence_id)
    except Exception as error:
        raise _translate(error) from error
    _private(response)
    return result
