from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError

from claim_api.auth import CurrentUser
from claim_api.case_service import (
    CaseNotFoundError,
    CaseService,
    StaleCaseError,
    UnsupportedActorError,
)
from claim_api.fixtures import SCENARIOS, UnknownScenarioError
from claim_api.models import (
    AttachQuoteRequest, CaseListItem, CaseListPage, CaseView, CreateCaseRequest, IntakePatch,
    RemoveQuoteRequest, ReportLineEditRequest, UpdateIntakeRequest, UpsertEstimateRequest,
)
from claim_api.postgres_cases import (
    DatabaseConfigurationError,
    DatabaseUnavailableError,
    PostgresCaseRepository,
)


router = APIRouter(prefix="/v1/cases", tags=["cases"])


def get_case_service() -> CaseService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "database_not_configured",
                "message": "DATABASE_URL is required for case operations.",
                "details": {},
            },
        )
    try:
        repository = PostgresCaseRepository(database_url)
    except DatabaseConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "database_tls_required",
                "message": "Configure DATABASE_URL with sslmode=require, or explicitly enable loopback-only plaintext for development.",
                "details": {},
            },
        ) from error
    return CaseService(repository)


CaseServiceDependency = Annotated[CaseService, Depends(get_case_service)]


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, UnknownScenarioError):
        return HTTPException(
            status_code=422,
            detail={
                "code": "unknown_scenario",
                "message": "The requested synthetic scenario does not exist.",
                "details": {"scenario_id": error.scenario_id, "available_scenarios": list(SCENARIOS)},
            },
        )
    if isinstance(error, UnsupportedActorError):
        return HTTPException(
            status_code=403,
            detail={"code": "invalid_actor", "message": str(error), "details": {}},
        )
    if isinstance(error, CaseNotFoundError):
        return HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Case not found.", "details": {}},
        )
    if isinstance(error, StaleCaseError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "stale_case",
                "message": "The case changed; refresh before editing.",
                "details": {"current_state_version": error.current_state_version},
            },
        )
    if isinstance(error, DatabaseUnavailableError):
        return HTTPException(
            status_code=503,
            detail={"code": "database_unavailable", "message": "Case storage is temporarily unavailable.", "details": {}},
        )
    if isinstance(error, (ValidationError, ValueError)):
        return HTTPException(
            status_code=422,
            detail={"code": "invalid_intake", "message": str(error), "details": {}},
        )
    raise error


@router.get("", response_model=list[CaseListItem])
def list_cases(
    user: CurrentUser,
    service: CaseServiceDependency,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[CaseListItem]:
    try:
        return service.list_cases(user.id, limit)
    except Exception as error:
        raise _translate_error(error) from error


@router.get("/page", response_model=CaseListPage)
def list_cases_page(
    user: CurrentUser,
    service: CaseServiceDependency,
    query: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
) -> CaseListPage:
    try:
        return service.list_cases_page(user.id, query, offset, limit)
    except Exception as error:
        raise _translate_error(error) from error


@router.post("", response_model=CaseView, status_code=status.HTTP_201_CREATED)
def create_case(request: CreateCaseRequest, user: CurrentUser, service: CaseServiceDependency) -> CaseView:
    try:
        return service.create_case(user.id, request.scenario_id)
    except Exception as error:
        raise _translate_error(error) from error


@router.get("/{case_id}", response_model=CaseView)
def get_case(case_id: UUID, user: CurrentUser, service: CaseServiceDependency) -> CaseView:
    try:
        return service.get_case(user.id, case_id)
    except Exception as error:
        raise _translate_error(error) from error


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(case_id: UUID, user: CurrentUser, service: CaseServiceDependency) -> Response:
    try:
        service.delete_case(user.id, case_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as error:
        raise _translate_error(error) from error


@router.patch("/{case_id}/intake", response_model=CaseView)
def update_intake(
    case_id: UUID,
    request: UpdateIntakeRequest,
    user: CurrentUser,
    service: CaseServiceDependency,
) -> CaseView:
    try:
        patch_data = request.model_dump(exclude={"expected_state_version"}, exclude_unset=True)
        patch = IntakePatch.model_validate(patch_data)
        return service.update_intake(user.id, case_id, request.expected_state_version, patch)
    except Exception as error:
        raise _translate_error(error) from error


def _translate_material_error(error: Exception) -> HTTPException:
    if isinstance(error, ValueError) and not isinstance(error, (UnsupportedActorError,)):
        return HTTPException(status_code=422, detail={
            "code": "invalid_case_material", "message": str(error), "details": {},
        })
    return _translate_error(error)


@router.patch("/{case_id}/report-lines/{line_id}", response_model=CaseView)
def edit_report_line(case_id: UUID, line_id: str, request: ReportLineEditRequest,
                     user: CurrentUser, service: CaseServiceDependency) -> CaseView:
    try:
        return service.edit_report_line(user.id, case_id, line_id, request)
    except Exception as error:
        raise _translate_material_error(error) from error


@router.put("/{case_id}/estimate", response_model=CaseView)
def upsert_estimate(case_id: UUID, request: UpsertEstimateRequest,
                    user: CurrentUser, service: CaseServiceDependency) -> CaseView:
    try:
        return service.upsert_estimate(user.id, case_id, request)
    except Exception as error:
        raise _translate_material_error(error) from error


@router.put("/{case_id}/quote", response_model=CaseView)
def attach_quote(case_id: UUID, request: AttachQuoteRequest,
                 user: CurrentUser, service: CaseServiceDependency) -> CaseView:
    try:
        return service.attach_quote(user.id, case_id, request)
    except Exception as error:
        raise _translate_material_error(error) from error


@router.delete("/{case_id}/quote", response_model=CaseView)
def remove_quote(case_id: UUID, request: RemoveQuoteRequest,
                 user: CurrentUser, service: CaseServiceDependency) -> CaseView:
    try:
        return service.remove_quote(user.id, case_id, request)
    except Exception as error:
        raise _translate_material_error(error) from error
