from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Annotated
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from claim_api.auth import CurrentUser
from claim_api.case_service import StaleCaseError, _actor_uuid
from claim_api.garage_repository import GarageRepository
from claim_api.garages import GarageError, GaragePreview, OsmGarageFinder, Point
from claim_api.models import ContractModel
from claim_api.routes.cases import CaseServiceDependency, _translate_error, get_case_service
from claim_api.twilio_sms import SmsError, TwilioSms

router = APIRouter(tags=["garage-sms"])


class PreviewRequest(ContractModel):
    expected_state_version: int = Field(ge=1)
    origin: Point | None = None


class SmsSettingsRequest(PreviewRequest):
    recipient: str = Field(pattern=r"^\+[1-9][0-9]{1,14}$")
    enabled: bool


def get_garage_repository():
    # Reuse the established database configuration and TLS checks.
    service = get_case_service()
    return GarageRepository(service.repository.database_url)


@lru_cache(maxsize=1)
def get_garage_finder():
    return OsmGarageFinder()


Repository = Annotated[GarageRepository, Depends(get_garage_repository)]
Finder = Annotated[OsmGarageFinder, Depends(get_garage_finder)]


def translate(error):
    if isinstance(error, (GarageError, SmsError)):
        status = 503 if error.code.startswith(("osm_", "twilio_not_", "invalid_sms_")) else 422
        return HTTPException(status, detail={"code": error.code, "message": error.code, "details": {}})
    return _translate_error(error)


@router.post("/v1/cases/{case_id}/garage-preview", response_model=GaragePreview)
def preview(case_id: UUID, request: PreviewRequest, user: CurrentUser, service: CaseServiceDependency, finder: Finder):
    try:
        case = service.get_case(user.id, case_id)
        if case.state_version != request.expected_state_version:
            raise StaleCaseError(case.state_version)
        return finder.preview(case.intake.location, request.origin)
    except Exception as error:
        raise translate(error) from error


@router.get("/v1/cases/{case_id}/garage-sms")
def sms_settings(case_id: UUID, user: CurrentUser, repository: Repository):
    try:
        return repository.settings(_actor_uuid(user.id), case_id)
    except Exception as error:
        raise translate(error) from error


@router.patch("/v1/cases/{case_id}/garage-sms")
def configure_sms(case_id: UUID, request: SmsSettingsRequest, user: CurrentUser, repository: Repository):
    try:
        actor = _actor_uuid(user.id)
        repository.configure(actor, case_id, request)
        return repository.settings(actor, case_id)
    except Exception as error:
        raise translate(error) from error


@router.post("/v1/cases/{case_id}/garage-sms/photo-received", status_code=202)
def existing_photos(case_id: UUID, user: CurrentUser, repository: Repository):
    try:
        actor = _actor_uuid(user.id)
        repository.enqueue_existing_photos(actor, case_id)
        return repository.settings(actor, case_id)
    except Exception as error:
        raise translate(error) from error


async def verified_fields(request: Request) -> dict[str, str]:
    client = TwilioSms()
    if request.url.query or request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(400, "Expected a form webhook without query parameters.")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 65536:
            raise HTTPException(413, "Webhook too large.")
    try:
        fields = parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=100)
        valid = client.validate_signature(request.url.path, fields, request.headers.get("X-Twilio-Signature", ""))
    except (UnicodeError, ValueError):
        raise HTTPException(400, "Invalid webhook form.") from None
    except SmsError as error:
        raise translate(error) from error
    if not valid or any(len(values) != 1 for values in fields.values()):
        raise HTTPException(403, "Invalid Twilio signature or ambiguous form.")
    flat = {key: values[0] for key, values in fields.items()}
    if flat.get("AccountSid") != client.account_sid:
        raise HTTPException(403, "Unexpected Twilio account.")
    return flat


Fields = Annotated[dict[str, str], Depends(verified_fields)]


@router.post("/v1/webhooks/twilio/inbound")
def incoming_mms(fields: Fields, repository: Repository):
    if fields.get("To") != os.getenv("TWILIO_PHONE_NUMBER", "").strip():
        raise HTTPException(403, "Unexpected receiving number.")
    sid, sender = fields.get("MessageSid", ""), fields.get("From", "")
    if not re.fullmatch(r"(?:SM|MM)[0-9a-fA-F]{32}", sid) or not re.fullmatch(r"\+[1-9][0-9]{1,14}", sender):
        raise HTTPException(400, "Invalid message identity.")
    try:
        count = int(fields.get("NumMedia", "0"))
    except ValueError:
        raise HTTPException(400, "Invalid media count.") from None
    if not 0 <= count <= 10:
        raise HTTPException(400, "Invalid media count.")
    media = []
    for index in range(count):
        mime = fields.get(f"MediaContentType{index}", "")
        url = fields.get(f"MediaUrl{index}", "")
        expected = f'https://api.twilio.com/2010-04-01/Accounts/{fields["AccountSid"]}/Messages/{sid}/Media/'
        if not url.startswith(expected) or not re.fullmatch(r"ME[0-9a-fA-F]{32}", url[len(expected):]):
            raise HTTPException(400, "Unexpected media URL.")
        if mime.startswith("image/"):
            media.append({"url": url, "mime_type": mime})
    if media:
        try:
            repository.enqueue_mms(sender, sid, media)
        except Exception as error:
            raise translate(error) from error
    return Response("<Response/>", media_type="application/xml")


@router.post("/v1/webhooks/twilio/status/{message_id}")
def sms_status(message_id: UUID, fields: Fields, repository: Repository):
    sid, status = fields.get("MessageSid", ""), fields.get("MessageStatus", "")
    if not re.fullmatch(r"SM[0-9a-fA-F]{32}", sid) or status not in {
        "accepted", "queued", "sending", "sent", "delivered", "undelivered", "failed", "canceled",
    }:
        raise HTTPException(400, "Invalid delivery status.")
    try:
        repository.callback(message_id, sid, status, fields.get("To", ""), fields.get("ErrorCode"))
    except Exception as error:
        raise translate(error) from error
    return Response(status_code=204)
