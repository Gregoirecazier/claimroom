"""Authenticated SMS invitation and private deposit conversation routes."""

from __future__ import annotations

import os
import re
from typing import Annotated
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from claim_api.auth import CurrentUser
from claim_api.deposit_grants import DepositGrantError, DepositGrantService
from claim_api.portal_chat import PortalChatMessage, PortalChatSend, PortalChatService
from claim_api.postgres_cases import DatabaseConfigurationError, DatabaseUnavailableError
from claim_api.sms_link import (
    SmsLinkCreateRequest, SmsLinkCreateResponse, SmsLinkService,
    TwilioSmsConfig, TwilioSmsGateway,
)
from claim_api.sms_models import SmsMessageView, SmsPreviewRequest
from claim_api.sms_recipient import RecipientLookupUnavailable, VapiCallerLookup, validate_e164
from claim_api.sms_service import SmsError


manager = APIRouter(prefix="/v1/cases/{case_id}/sms-links", tags=["SMS deposit link"])
guest = APIRouter(prefix="/v1/deposit/conversation", tags=["insured conversation"])
webhook = APIRouter(prefix="/v1/webhooks/twilio/sms", tags=["SMS callbacks"])


def _error(error: Exception) -> HTTPException:
    if isinstance(error, SmsError):
        return HTTPException(error.status_code, detail={"code": error.code, "message": str(error), "details": {}})
    if isinstance(error, DepositGrantError):
        code = error.code
        status = 404 if code == "not_found" else 401 if code in {"invalid_session", "invalid_grant"} else 409
        return HTTPException(status, detail={"code": code, "message": "Private link is unavailable.", "details": {}})
    if isinstance(error, (DatabaseConfigurationError, DatabaseUnavailableError)):
        return HTTPException(503, detail={"code": "database_unavailable", "message": "Storage is unavailable.", "details": {}})
    raise error


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def get_sms_link_service() -> SmsLinkService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    portal_url = os.getenv("DEPOSIT_PORTAL_BASE_URL", "").strip()
    if not database_url or not portal_url:
        raise HTTPException(503, "SMS deposit link is not configured")
    try:
        config = TwilioSmsConfig.from_env()
        lookup = VapiCallerLookup.from_env() if os.getenv("VAPI_PRIVATE_API_KEY") else None
        return SmsLinkService(database_url, DepositGrantService(database_url, portal_url),
                              lookup, TwilioSmsGateway(config))
    except (ValueError, DatabaseConfigurationError, RecipientLookupUnavailable) as error:
        raise HTTPException(503, "SMS deposit link is not configured") from error


def get_chat_service() -> PortalChatService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(503, "Conversation is not configured")
    try:
        return PortalChatService(database_url)
    except DatabaseConfigurationError as error:
        raise HTTPException(503, "Conversation is not configured") from error


SmsService = Annotated[SmsLinkService, Depends(get_sms_link_service)]
ChatService = Annotated[PortalChatService, Depends(get_chat_service)]


@manager.get("/recipient-candidate")
def candidate(case_id: UUID, user: CurrentUser, service: SmsService) -> dict[str, str]:
    try:
        return {"recipient_masked": service.caller_candidate(UUID(user.id), case_id)}
    except Exception as error:
        raise _error(error) from error


@manager.post("/preview")
def preview(case_id: UUID, request: SmsPreviewRequest, user: CurrentUser,
            service: SmsService, response: Response) -> dict:
    try:
        result = service.preview_sms_link(UUID(user.id), case_id, request)
    except Exception as error:
        raise _error(error) from error
    _private(response)
    return result


@manager.post("", response_model=SmsLinkCreateResponse, status_code=201)
def send(case_id: UUID, request: SmsLinkCreateRequest, user: CurrentUser,
         service: SmsService, response: Response) -> SmsLinkCreateResponse:
    try:
        result = service.create_sms_link(UUID(user.id), case_id, request)
        if not result.replayed:
            result.message = service.dispatch(UUID(user.id), case_id, result.message.id)
    except Exception as error:
        raise _error(error) from error
    response.status_code = 200 if result.replayed else 201
    _private(response)
    return result


@manager.get("", response_model=list[SmsMessageView])
def list_links(case_id: UUID, user: CurrentUser, service: SmsService,
               response: Response) -> list[SmsMessageView]:
    try:
        result = service.list_links(UUID(user.id), case_id)
    except Exception as error:
        raise _error(error) from error
    _private(response)
    return result


@manager.get("/{message_id}/link")
def reveal_link(case_id: UUID, message_id: UUID, user: CurrentUser,
                service: SmsService, response: Response) -> dict:
    try:
        result = service.link(UUID(user.id), case_id, message_id)
    except Exception as error:
        raise _error(error) from error
    _private(response)
    return result


@manager.get("/{message_id}/conversation", response_model=list[PortalChatMessage])
def manager_messages(case_id: UUID, message_id: UUID, user: CurrentUser,
                     service: ChatService, response: Response) -> list[PortalChatMessage]:
    try:
        result = service.list_manager(UUID(user.id), case_id, message_id)
    except Exception as error:
        raise _error(error) from error
    _private(response)
    return result


@manager.post("/{message_id}/conversation", response_model=PortalChatMessage, status_code=201)
def manager_reply(case_id: UUID, message_id: UUID, request: PortalChatSend,
                  user: CurrentUser, service: ChatService, response: Response) -> PortalChatMessage:
    try:
        result = service.send_manager(UUID(user.id), case_id, message_id, request)
    except Exception as error:
        raise _error(error) from error
    _private(response)
    return result


@guest.get("", response_model=list[PortalChatMessage])
def guest_messages(request: Request, service: ChatService, response: Response) -> list[PortalChatMessage]:
    token = _bearer(request)
    try:
        result = service.list_guest(token)
    except Exception as error:
        raise _error(error) from error
    _private(response)
    return result


@guest.post("", response_model=PortalChatMessage, status_code=201)
def guest_reply(message: PortalChatSend, request: Request,
                service: ChatService, response: Response) -> PortalChatMessage:
    token = _bearer(request)
    try:
        result = service.send_guest(token, message)
    except Exception as error:
        raise _error(error) from error
    _private(response)
    return result


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Guest session is required")
    return token


@webhook.post("/status")
async def sms_status(request: Request) -> Response:
    try:
        config = TwilioSmsConfig.from_env()
    except ValueError as error:
        raise HTTPException(503, "SMS callback is not configured") from error
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected form data")
    raw = await request.body()
    if len(raw) > 16_384:
        raise HTTPException(413, "Webhook is too large")
    try:
        pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
        query = request.scope.get("query_string", b"").decode("ascii")
        message_id = UUID(request.query_params["message_id"])
    except (UnicodeDecodeError, ValueError, KeyError) as error:
        raise HTTPException(400, "Invalid callback") from error
    params = dict(pairs)
    if len(params) != len(pairs) or query != f"message_id={message_id}":
        raise HTTPException(400, "Invalid callback")
    if not config.valid_callback(config.callback_url(message_id), params,
                                 request.headers.get("x-twilio-signature", "")):
        raise HTTPException(403, "Invalid Twilio signature")
    try:
        sid = params["MessageSid"]
        recipient = validate_e164(params["To"])
        if (params["AccountSid"] != config.account_sid or
                params.get("MessagingServiceSid", config.messaging_service_sid) != config.messaging_service_sid or
                not re.fullmatch(r"(?:SM|MM)[0-9a-fA-F]{32}", sid)):
            raise ValueError("Wrong account or SMS")
    except (KeyError, ValueError) as error:
        raise HTTPException(403, "Wrong SMS account") from error
    try:
        service = get_sms_link_service()
        if not service.apply_status(message_id, sid, recipient, params.get("MessageStatus", ""),
                                    params.get("ErrorCode")):
            raise HTTPException(404, "SMS not found")
    except (DatabaseConfigurationError, DatabaseUnavailableError) as error:
        raise HTTPException(503, "SMS storage unavailable") from error
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
