"""Manager-only issue and revocation of insured deposit links."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from claim_api.auth import CurrentUser
from claim_api.deposit_grants import DepositGrantError, DepositGrantService
from claim_api.postgres_cases import DatabaseUnavailableError


router = APIRouter(prefix="/v1/cases/{case_id}/deposit-grants", tags=["deposit-grants"])


class IssueGrantRequest(BaseModel):
    expected_state_version: int = Field(ge=0)
    content_revision: int = Field(gt=0)
    ttl_seconds: int = Field(default=86400, ge=60, le=604800)


class GrantResponse(BaseModel):
    grant_id: UUID
    url: str
    expires_at: datetime


class RevokeGrantRequest(BaseModel):
    expected_state_version: int = Field(ge=0)


def get_deposit_grant_service() -> DepositGrantService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    base = os.getenv("DEPOSIT_PORTAL_BASE_URL", "").strip()
    if not database_url or not base:
        raise HTTPException(status_code=503, detail={
            "code": "deposit_grants_not_configured", "message": "Deposit links are not configured.", "details": {},
        })
    try:
        return DepositGrantService(database_url, base)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "deposit_grants_not_configured", "message": "Deposit links are not configured.", "details": {},
        }) from exc


Service = Annotated[DepositGrantService, Depends(get_deposit_grant_service)]


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, DepositGrantError):
        statuses = {"not_found": 404, "stale_case": 409, "invalid_ttl": 422,
                    "forbidden_capability": 403}
        return HTTPException(status_code=statuses.get(exc.code, 400), detail={
            "code": exc.code, "message": "Deposit grant request could not be completed.", "details": {},
        })
    if isinstance(exc, DatabaseUnavailableError):
        return HTTPException(status_code=503, detail={
            "code": "database_unavailable", "message": "Deposit grant storage is unavailable.", "details": {},
        })
    raise exc


@router.post("", response_model=GrantResponse, status_code=201)
def issue_grant(case_id: UUID, request: IssueGrantRequest, user: CurrentUser,
                service: Service, response: Response) -> GrantResponse:
    try:
        issued = service.issue(case_id, request.content_revision,
                               timedelta(seconds=request.ttl_seconds), actor_id=UUID(user.id),
                               expected_state_version=request.expected_state_version)
    except Exception as exc:
        raise _translate(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return GrantResponse(**issued.__dict__)


@router.post("/{grant_id}/revoke", status_code=204)
def revoke_grant(case_id: UUID, grant_id: UUID, request: RevokeGrantRequest,
                 user: CurrentUser, service: Service) -> Response:
    try:
        service.revoke(grant_id, case_id, actor_id=UUID(user.id),
                       expected_state_version=request.expected_state_version)
    except Exception as exc:
        raise _translate(exc) from exc
    return Response(status_code=204)
