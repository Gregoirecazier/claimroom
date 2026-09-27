from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse

from claim_api.auth import CurrentUser
from claim_api.case_service import CaseNotFoundError, StaleCaseError, UnsupportedActorError
from claim_api.postgres_cases import DatabaseConfigurationError, DatabaseUnavailableError, PostgresCaseRepository
from claim_api.review_models import (
    ActionRequest,
    ActionReceiptView,
    ApproveDraftRequest,
    ApprovalView,
    DraftEditRequest,
    DraftView,
    TransmissionPreviewView,
)
from claim_api.review_service import ReviewActionsService, ReviewTransitionError


router = APIRouter(prefix="/v1/cases", tags=["review and simulated actions"])


def get_review_actions_service() -> ReviewActionsService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "database_not_configured", "message": "DATABASE_URL is required for case operations.", "details": {}},
        )
    try:
        return ReviewActionsService(PostgresCaseRepository(database_url))
    except DatabaseConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "database_tls_required", "message": str(error), "details": {}},
        ) from error


ReviewServiceDependency = Annotated[ReviewActionsService, Depends(get_review_actions_service)]


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, CaseNotFoundError):
        return HTTPException(status_code=404, detail={"code": "not_found", "message": "Case not found.", "details": {}})
    if isinstance(error, UnsupportedActorError):
        return HTTPException(status_code=403, detail={"code": "invalid_actor", "message": str(error), "details": {}})
    if isinstance(error, StaleCaseError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "stale_case",
                "message": "The case changed; refresh before continuing.",
                "details": {"current_state_version": error.current_state_version},
            },
        )
    if isinstance(error, ReviewTransitionError):
        status_code = 403 if error.code == "forbidden_approval" else 409 if error.code in {"stale_draft", "idempotency_conflict", "reconciliation_required"} else 422
        details = dict(error.details)
        if "reason_codes" not in details:
            if details.get("failed_gates"):
                reasons = [code for gate in details["failed_gates"] for code in gate.get("reason_codes", [])]
                details["reason_codes"] = reasons or ["upstream_gates_not_passed"]
            elif details.get("missing_fields"):
                details["reason_codes"] = list(details["missing_fields"])
            elif "quote_total_minor" in details:
                details["reason_codes"] = ["quote_mismatch"]
            elif "draft_amount_minor" in details:
                details["reason_codes"] = ["estimate_draft_mismatch"]
            elif "quote_evidence_id" in details:
                details["reason_codes"] = ["quote_attachment_required"]
            elif "quote_estimate_version" in details:
                details["reason_codes"] = ["quote_outdated"]
            else:
                details["reason_codes"] = [error.code]
        return HTTPException(
            status_code=status_code,
            detail={"code": error.code, "message": str(error), "details": details},
        )
    if isinstance(error, DatabaseUnavailableError):
        return HTTPException(
            status_code=503,
            detail={"code": "database_unavailable", "message": "Case storage is temporarily unavailable.", "details": {}},
        )
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail={"code": "invalid_draft", "message": str(error), "details": {}})
    raise error


@router.patch("/{case_id}/drafts/current", response_model=DraftView)
def update_current_draft(
    case_id: UUID,
    request: DraftEditRequest,
    user: CurrentUser,
    service: ReviewServiceDependency,
) -> DraftView:
    try:
        return service.update_draft(user.id, case_id, request)
    except Exception as error:
        raise _translate_error(error) from error


@router.post("/{case_id}/approvals", response_model=ApprovalView, status_code=status.HTTP_201_CREATED)
def approve_current_draft(
    case_id: UUID,
    request: ApproveDraftRequest,
    user: CurrentUser,
    service: ReviewServiceDependency,
    response: Response,
) -> ApprovalView:
    try:
        return service.approve(user.id, case_id, request)
    except Exception as error:
        raise _translate_error(error) from error


@router.post("/{case_id}/registration", response_model=ActionReceiptView, status_code=status.HTTP_201_CREATED)
def simulate_registration(
    case_id: UUID,
    request: ActionRequest,
    user: CurrentUser,
    service: ReviewServiceDependency,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> ActionReceiptView:
    try:
        result = service.register(user.id, case_id, request, idempotency_key)
        if not result.created:
            response.status_code = status.HTTP_200_OK
        return result.receipt
    except Exception as error:
        raise _translate_error(error) from error


@router.post("/{case_id}/send", response_model=ActionReceiptView, status_code=status.HTTP_201_CREATED)
def simulate_send(
    case_id: UUID,
    request: ActionRequest,
    user: CurrentUser,
    service: ReviewServiceDependency,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> ActionReceiptView:
    try:
        result = service.send(user.id, case_id, request, idempotency_key)
        if not result.created:
            response.status_code = status.HTTP_200_OK
        return result.receipt
    except Exception as error:
        raise _translate_error(error) from error


@router.get("/{case_id}/transmission/preview", response_model=TransmissionPreviewView)
def transmission_preview(case_id: UUID, user: CurrentUser, service: ReviewServiceDependency) -> TransmissionPreviewView:
    try:
        return service.preview(user.id, case_id)
    except Exception as error:
        raise _translate_error(error) from error


@router.get("/{case_id}/transmission/receipt")
def download_transmission_receipt(case_id: UUID, user: CurrentUser, service: ReviewServiceDependency) -> JSONResponse:
    try:
        envelope = service.receipt(user.id, case_id)
        return JSONResponse(
            content=envelope,
            headers={"Content-Disposition": f'attachment; filename="simulation-receipt-{case_id}.json"',
                     "Cache-Control": "private, no-store"},
        )
    except Exception as error:
        raise _translate_error(error) from error
