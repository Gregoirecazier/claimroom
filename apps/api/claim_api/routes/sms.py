"""Authenticated manager endpoints for simulated WhatsApp follow-up."""

from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from claim_api.auth import CurrentUser
from claim_api.deposit_grants import DepositGrantError, DepositGrantService
from claim_api.postgres_cases import DatabaseConfigurationError, DatabaseUnavailableError
from claim_api.sms_models import (
    SmsCreateRequest, SmsCreateResponse, SmsLinkResponse, SmsMessageView,
    SmsMockTransitionRequest, SmsMockTransitionResponse, SmsPreview, SmsPreviewRequest, WhatsAppInboundView,
)
from claim_api.sms_recipient import RecipientLookupUnavailable, VapiCallerLookup
from claim_api.sms_service import CaseMessageService, SmsError
from claim_api.fake_whatsapp import FakeWhatsAppGateway


router = APIRouter(prefix="/v1/cases/{case_id}/messages", tags=["follow-up WhatsApp"])


def get_message_service() -> CaseMessageService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    portal_url = os.getenv("DEPOSIT_PORTAL_BASE_URL", "").strip()
    if not database_url or not portal_url:
        raise HTTPException(503, detail={"code": "sms_not_configured", "message": "WhatsApp preview is not configured.", "details": {}})
    try:
        grants = DepositGrantService(database_url, portal_url)
        lookup = VapiCallerLookup.from_env() if os.getenv("VAPI_PRIVATE_API_KEY") else None
        mode = os.getenv("WHATSAPP_DELIVERY_MODE", "mock").strip().lower()
        if mode not in {"mock", "fake_whatsapp"}:
            raise ValueError("Unsupported WhatsApp delivery mode")
        gateway = FakeWhatsAppGateway() if mode == "fake_whatsapp" else None
        return CaseMessageService(database_url, grants, lookup, gateway)
    except (ValueError, DatabaseConfigurationError, RecipientLookupUnavailable) as error:
        raise HTTPException(503, detail={"code": "sms_not_configured", "message": "WhatsApp preview is not configured.", "details": {}}) from error


Service = Annotated[CaseMessageService, Depends(get_message_service)]


def get_fake_message_service() -> CaseMessageService:
    if os.getenv("SMS_LINK_DELIVERY_MODE", "disabled").strip().lower() == "twilio":
        raise HTTPException(404, detail={"code": "fake_whatsapp_not_configured",
                                         "message": "Fake WhatsApp is unavailable.", "details": {}})
    if os.getenv("WHATSAPP_DELIVERY_MODE", "mock").strip().lower() != "fake_whatsapp":
        raise HTTPException(404, detail={"code": "fake_whatsapp_not_configured",
                                         "message": "Fake WhatsApp is unavailable.", "details": {}})
    return get_message_service()


FakeService = Annotated[CaseMessageService, Depends(get_fake_message_service)]


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, SmsError):
        return HTTPException(error.status_code, detail={
            "code": error.code, "message": str(error), "details": error.details,
        })
    if isinstance(error, DepositGrantError):
        status = {"not_found": 404, "stale_case": 409, "expired_grant": 409,
                  "revoked_grant": 409, "forbidden_capability": 403}.get(error.code, 409)
        return HTTPException(status, detail={"code": error.code, "message": "Deposit link is unavailable.", "details": {}})
    if isinstance(error, DatabaseUnavailableError):
        return HTTPException(503, detail={"code": "database_unavailable", "message": "WhatsApp storage is unavailable.", "details": {}})
    raise error


@router.get("/recipient-candidate")
def recipient_candidate(case_id: UUID, user: CurrentUser, service: Service) -> dict[str, str]:
    try:
        return {"recipient_masked": service.caller_candidate(UUID(user.id), case_id)}
    except Exception as error:
        raise _translate(error) from error


@router.post("/preview", response_model=SmsPreview)
def preview_sms(case_id: UUID, request: SmsPreviewRequest, user: CurrentUser,
                service: Service, response: Response) -> SmsPreview:
    try:
        preview = service.preview(UUID(user.id), case_id, request)
    except Exception as error:
        raise _translate(error) from error
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return preview


@router.post("/auto-fake", response_model=SmsMessageView)
def auto_fake_sms(case_id: UUID, user: CurrentUser, service: FakeService,
                  response: Response) -> SmsMessageView:
    try:
        message = service.auto_fake_follow_up(UUID(user.id), case_id)
    except Exception as error:
        raise _translate(error) from error
    response.headers["Cache-Control"] = "no-store"
    return message


@router.post("", response_model=SmsCreateResponse, status_code=201)
def create_sms(case_id: UUID, request: SmsCreateRequest, user: CurrentUser,
               service: Service, response: Response) -> SmsCreateResponse:
    try:
        result = service.create(UUID(user.id), case_id, request)
        if result.message.provider == FakeWhatsAppGateway.provider and result.message.status == "queued":
            result.message = service.dispatch_fake(UUID(user.id), case_id, result.message.id)
    except Exception as error:
        raise _translate(error) from error
    response.status_code = 200 if result.replayed else 201
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("", response_model=list[SmsMessageView])
def list_sms(case_id: UUID, user: CurrentUser, service: Service,
             response: Response) -> list[SmsMessageView]:
    try:
        result = service.list_messages(UUID(user.id), case_id)
    except Exception as error:
        raise _translate(error) from error
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/inbound", response_model=list[WhatsAppInboundView])
def list_inbound(case_id: UUID, user: CurrentUser, service: Service,
                 response: Response) -> list[WhatsAppInboundView]:
    try:
        result = service.list_inbound(UUID(user.id), case_id)
    except Exception as error:
        raise _translate(error) from error
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{message_id}/mock-transition", response_model=SmsMockTransitionResponse)
def mock_transition(case_id: UUID, message_id: UUID, request: SmsMockTransitionRequest,
                    user: CurrentUser, service: Service, response: Response) -> SmsMockTransitionResponse:
    try:
        result = service.mock_transition(UUID(user.id), case_id, message_id, request)
    except Exception as error:
        raise _translate(error) from error
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{message_id}/link", response_model=SmsLinkResponse)
def message_link(case_id: UUID, message_id: UUID, user: CurrentUser,
                 service: Service, response: Response) -> SmsLinkResponse:
    try:
        result = service.link(UUID(user.id), case_id, message_id)
    except Exception as error:
        raise _translate(error) from error
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return result
