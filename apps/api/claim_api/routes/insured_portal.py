"""Guest deposit routes. Manager JWTs are never accepted as guest sessions."""

from __future__ import annotations

import os
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import Response as BinaryResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ValidationError

from claim_api.case_service import CaseReadOnlyError, StaleCaseError
from claim_api.deposit_grants import DepositGrantError
from claim_api.evidence_service import (
    EvidenceNotFoundError, InvalidEvidenceUploadError, UploadIntentExpiredError,
)
from claim_api.insured_portal import InsuredPortalService
from claim_api.deposit_chat import ChatRequest, ChatResponse, ChatUnavailable, reply as chat_reply
from claim_api.deposit_history import SaveHistoryRequest
from claim_api.models import CreateEvidenceUploadIntentRequest, FinalizeEvidenceRequest
from claim_api.storage import StorageAdapterError, StorageConfigurationError, StorageUnavailableError
from claim_api.postgres_cases import DatabaseConfigurationError, DatabaseUnavailableError


router = APIRouter(prefix="/v1/deposit", tags=["insured-deposit"])
_bearer = HTTPBearer(auto_error=False)


def get_portal_service() -> InsuredPortalService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(status_code=503, detail={"code": "database_unavailable", "message": "Deposit service unavailable.", "details": {}})
    try:
        return InsuredPortalService(database_url)
    except DatabaseConfigurationError as exc:
        raise HTTPException(status_code=503, detail={"code": "database_unavailable",
                             "message": "Deposit service is temporarily unavailable.", "details": {}}) from exc


Portal = Annotated[InsuredPortalService, Depends(get_portal_service)]


def bearer(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail={"code": "invalid_session", "message": "Open your deposit link again.", "details": {}})
    return credentials.credentials


Bearer = Annotated[str, Depends(bearer)]


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, DepositGrantError):
        status = {"invalid_grant": 401, "invalid_session": 401, "expired_session": 401,
                  "expired_grant": 410, "revoked_grant": 410, "forbidden_capability": 403,
                  "case_read_only": 409, "stale_chat_history": 409, "invalid_upload": 422}.get(exc.code, 400)
        return HTTPException(status_code=status, detail={"code": exc.code,
                             "message": "The deposit action could not be completed.", "details": {}})
    if isinstance(exc, StaleCaseError):
        return HTTPException(status_code=409, detail={"code": "stale_case",
                             "message": "The case changed. Refresh and retry.",
                             "details": {"current_state_version": exc.current_state_version}})
    if isinstance(exc, CaseReadOnlyError):
        return HTTPException(status_code=409, detail={"code": "case_read_only",
                             "message": "This case can no longer be changed.", "details": {}})
    if isinstance(exc, (EvidenceNotFoundError,)):
        return HTTPException(status_code=404, detail={"code": "not_found", "message": "Item not found.", "details": {}})
    if isinstance(exc, (InvalidEvidenceUploadError, UploadIntentExpiredError, ValidationError, ValueError)):
        return HTTPException(status_code=422, detail={"code": "invalid_upload",
                             "message": "Check the file or correction and retry.", "details": {}})
    if isinstance(exc, (StorageAdapterError, StorageConfigurationError, StorageUnavailableError)):
        return HTTPException(status_code=503, detail={"code": "storage_unavailable",
                             "message": "Private storage is temporarily unavailable.", "details": {}})
    if isinstance(exc, (DatabaseUnavailableError, psycopg.OperationalError, psycopg.InterfaceError)):
        return HTTPException(status_code=503, detail={"code": "database_unavailable",
                             "message": "Deposit service is temporarily unavailable.", "details": {}})
    raise exc


@router.get("/session")
def exchange_link(token: Bearer, service: Portal, response: Response) -> dict[str, Any]:
    try:
        result = service.exchange(token)
    except Exception as exc:
        raise _error(exc) from exc
    _private(response)
    return result


@router.get("/summary")
def summary(token: Bearer, service: Portal, response: Response) -> dict[str, Any]:
    try:
        result = service.summary(token)
    except Exception as exc:
        raise _error(exc) from exc
    _private(response)
    return result


@router.get("/chat-history")
def chat_history(token: Bearer, service: Portal, response: Response) -> dict[str, Any]:
    try:
        result = service.chat_history(token)
    except Exception as exc:
        raise _error(exc) from exc
    _private(response)
    return result


@router.put("/chat-history")
def save_chat_history(request: SaveHistoryRequest, token: Bearer, service: Portal,
                      response: Response) -> dict[str, Any]:
    try:
        result = service.save_chat_history(token, request.expected_revision, request.state.model_dump(mode="json"))
    except Exception as exc:
        raise _error(exc) from exc
    _private(response)
    return result


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, token: Bearer, service: Portal, response: Response) -> ChatResponse:
    try:
        result = chat_reply(service, token, request)
    except ChatUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "chat_unavailable",
                            "message": "The chat assistant is not configured.", "details": {}}) from exc
    except (TimeoutError, ValueError) as exc:
        raise HTTPException(status_code=503, detail={"code": "chat_unavailable",
                            "message": "The chat assistant is temporarily unavailable.", "details": {}}) from exc
    except (DepositGrantError, DatabaseUnavailableError, psycopg.OperationalError,
            psycopg.InterfaceError) as exc:
        raise _error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "chat_unavailable",
                            "message": "The chat assistant is temporarily unavailable.", "details": {}}) from exc
    _private(response)
    return result


class CorrectionRequest(BaseModel):
    expected_state_version: int = Field(ge=1)
    field: str = Field(min_length=1, max_length=64)
    value: str | None
    reason: str | None = Field(default=None, max_length=500)


@router.post("/corrections")
def correct(request: CorrectionRequest, token: Bearer, service: Portal, response: Response) -> dict[str, Any]:
    try:
        result = service.correct(token, request.expected_state_version, request.field,
                                 request.value, request.reason)
    except Exception as exc:
        raise _error(exc) from exc
    _private(response)
    return result


@router.post("/evidence/upload-intents", status_code=201)
def upload_intent(request: CreateEvidenceUploadIntentRequest, token: Bearer,
                  service: Portal, response: Response) -> dict[str, Any]:
    try:
        result = service.create_upload_intent(token, request)
    except Exception as exc:
        raise _error(exc) from exc
    _private(response)
    return result.model_dump(mode="json")


@router.post("/evidence", status_code=201)
def finalize(request: FinalizeEvidenceRequest, token: Bearer,
             service: Portal, response: Response) -> dict[str, Any]:
    try:
        result = service.finalize_upload(token, request)
    except Exception as exc:
        raise _error(exc) from exc
    _private(response)
    return result


@router.get("/evidence/{evidence_id}/read-url")
def read_url(evidence_id: UUID, token: Bearer, service: Portal, response: Response) -> dict[str, Any]:
    try:
        result = service.signed_read_url(token, evidence_id)
    except Exception as exc:
        raise _error(exc) from exc
    _private(response)
    return result


@router.get("/demo-media")
def demo_media(token: Bearer, service: Portal, response: Response) -> list[dict[str, Any]]:
    try:
        result = service.demo_media(token)
    except Exception as exc:
        raise _error(exc) from exc
    _private(response)
    return result


@router.get("/demo-media/{media_key}/preview")
def demo_preview(media_key: str, token: Bearer, service: Portal) -> BinaryResponse:
    try:
        data, mime = service.demo_preview(token, media_key)
    except Exception as exc:
        raise _error(exc) from exc
    return BinaryResponse(content=data, media_type=mime,
                          headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


class AttachDemoRequest(BaseModel):
    expected_state_version: int = Field(ge=1)


@router.post("/demo-media/{media_key}/attach", status_code=201)
def attach_demo(media_key: str, request: AttachDemoRequest, token: Bearer,
                service: Portal, response: Response) -> dict[str, Any]:
    try:
        result = service.attach_demo(token, media_key, request.expected_state_version)
    except Exception as exc:
        raise _error(exc) from exc
    _private(response)
    return result
