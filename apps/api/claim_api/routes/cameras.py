from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from claim_api.auth import CurrentUser
from claim_api.camera_service import CameraService
from claim_api.case_service import CaseNotFoundError, StaleCaseError, UnsupportedActorError
from claim_api.models import (
    CameraMapView, CameraRequestTransition, CameraSearchView, CaseView,
    CreateCameraRequest, UpdateCameraRequestStatus,
)
from claim_api.postgres_cases import DatabaseConfigurationError, DatabaseUnavailableError, PostgresCaseRepository


router = APIRouter(prefix="/v1/cases", tags=["cameras"])


def get_camera_service() -> CameraService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(status_code=503, detail={
            "code": "database_not_configured", "message": "DATABASE_URL is required.", "details": {},
        })
    try:
        return CameraService(PostgresCaseRepository(database_url))
    except DatabaseConfigurationError as error:
        raise HTTPException(status_code=503, detail={
            "code": "database_not_configured", "message": str(error), "details": {},
        }) from error


CameraServiceDependency = Annotated[CameraService, Depends(get_camera_service)]


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, CaseNotFoundError):
        return HTTPException(status_code=404, detail={
            "code": "not_found", "message": "Case not found.", "details": {},
        })
    if isinstance(error, UnsupportedActorError):
        return HTTPException(status_code=403, detail={
            "code": "invalid_actor", "message": str(error), "details": {},
        })
    if isinstance(error, StaleCaseError):
        return HTTPException(status_code=409, detail={
            "code": "stale_case", "message": str(error),
            "details": {"current_state_version": error.current_state_version},
        })
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail={
            "code": "invalid_camera_request", "message": str(error), "details": {},
        })
    if isinstance(error, DatabaseUnavailableError):
        return HTTPException(status_code=503, detail={
            "code": "database_unavailable", "message": "Case storage is unavailable.", "details": {},
        })
    raise error


@router.get("/{case_id}/cameras/search", response_model=CameraSearchView)
def search_cameras(case_id: UUID, user: CurrentUser,
                   service: CameraServiceDependency) -> CameraSearchView:
    try:
        return service.search(user.id, case_id)
    except Exception as error:
        raise _translate(error) from error


@router.get("/{case_id}/cameras/map", response_model=CameraMapView)
def map_cameras(case_id: UUID, user: CurrentUser, service: CameraServiceDependency,
                radius_m: int = Query(default=150, ge=1, le=500)) -> CameraMapView:
    try:
        return service.map(user.id, case_id, radius_m=radius_m)
    except Exception as error:
        raise _translate(error) from error


@router.post("/{case_id}/camera-requests", response_model=CaseView, status_code=201)
def create_camera_request(case_id: UUID, request: CreateCameraRequest,
                          user: CurrentUser, service: CameraServiceDependency) -> CaseView:
    try:
        return service.create_draft(user.id, case_id, request)
    except Exception as error:
        raise _translate(error) from error


@router.post("/{case_id}/camera-requests/{request_id}/approve", response_model=CaseView)
def approve_camera_request(case_id: UUID, request_id: UUID,
                           request: CameraRequestTransition, user: CurrentUser,
                           service: CameraServiceDependency) -> CaseView:
    try:
        return service.approve(user.id, case_id, request_id, request)
    except Exception as error:
        raise _translate(error) from error


@router.patch("/{case_id}/camera-requests/{request_id}/status", response_model=CaseView)
def update_camera_request_status(case_id: UUID, request_id: UUID,
                                 request: UpdateCameraRequestStatus, user: CurrentUser,
                                 service: CameraServiceDependency) -> CaseView:
    try:
        return service.set_status(user.id, case_id, request_id, request)
    except Exception as error:
        raise _translate(error) from error
