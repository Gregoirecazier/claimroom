from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from claim_api.auth import CurrentUser
from claim_api.case_service import CaseNotFoundError, StaleCaseError, UnsupportedActorError
from claim_api.evidence_service import (
    CCTVFixtureUnavailableError,
    G1MediaUnavailableError,
    EvidenceNotFoundError,
    EvidenceService,
    InvalidEvidenceUploadError,
    UploadIntentExpiredError,
)
from claim_api.models import (
    CaseView,
    CreateEvidenceUploadIntentRequest,
    EvidenceReadUrlView,
    EvidenceUploadIntentView,
    FinalizeEvidenceRequest,
    ReceiveCCTVRequest,
    SeedG1MediaRequest,
)
from claim_api.postgres_cases import DatabaseConfigurationError, DatabaseUnavailableError, PostgresCaseRepository
from claim_api.storage import (
    StorageAdapterError,
    StorageConfigurationError,
    StorageUnavailableError,
    SupabaseStorageAdapter,
)


router = APIRouter(prefix="/v1/cases", tags=["evidence"])


def get_evidence_service() -> EvidenceService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "database_not_configured", "message": "DATABASE_URL is required for case operations.", "details": {}},
        )
    try:
        repository = PostgresCaseRepository(database_url)
        storage = SupabaseStorageAdapter.from_env()
    except (DatabaseConfigurationError, StorageConfigurationError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "evidence_storage_not_configured", "message": str(error), "details": {}},
        ) from error
    from claim_api.media_workflow import MediaWorkflow, MediaWorkflowRepository
    return EvidenceService(repository, storage,
        on_media_received=MediaWorkflow(MediaWorkflowRepository(database_url)).received)


EvidenceServiceDependency = Annotated[EvidenceService, Depends(get_evidence_service)]


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, (CaseNotFoundError, EvidenceNotFoundError)):
        return HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Case or evidence not found.", "details": {}},
        )
    if isinstance(error, UnsupportedActorError):
        return HTTPException(
            status_code=403,
            detail={"code": "invalid_actor", "message": str(error), "details": {}},
        )
    if isinstance(error, StaleCaseError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "stale_case",
                "message": "The case changed; refresh before completing this evidence action.",
                "details": {"current_state_version": error.current_state_version},
            },
        )
    if isinstance(error, (InvalidEvidenceUploadError, UploadIntentExpiredError, CCTVFixtureUnavailableError, G1MediaUnavailableError)):
        return HTTPException(
            status_code=422,
            detail={"code": "invalid_evidence", "message": str(error), "details": {}},
        )
    if isinstance(error, (StorageConfigurationError, StorageUnavailableError, StorageAdapterError)):
        return HTTPException(
            status_code=503,
            detail={"code": "storage_unavailable", "message": "Private evidence storage is unavailable or misconfigured.", "details": {}},
        )
    if isinstance(error, DatabaseUnavailableError):
        return HTTPException(
            status_code=503,
            detail={"code": "database_unavailable", "message": "Case storage is temporarily unavailable.", "details": {}},
        )
    raise error


@router.post("/{case_id}/evidence/upload-intents", response_model=EvidenceUploadIntentView, status_code=201)
def create_upload_intent(
    case_id: UUID,
    request: CreateEvidenceUploadIntentRequest,
    user: CurrentUser,
    service: EvidenceServiceDependency,
) -> EvidenceUploadIntentView:
    try:
        return service.create_upload_intent(user.id, case_id, request)
    except Exception as error:
        raise _translate_error(error) from error


@router.post("/{case_id}/evidence", response_model=CaseView, status_code=201)
def finalize_evidence(
    case_id: UUID,
    request: FinalizeEvidenceRequest,
    user: CurrentUser,
    service: EvidenceServiceDependency,
) -> CaseView:
    try:
        return service.finalize_upload(user.id, case_id, request)
    except Exception as error:
        raise _translate_error(error) from error


@router.get("/{case_id}/evidence/{evidence_id}/read-url", response_model=EvidenceReadUrlView)
def evidence_read_url(
    case_id: UUID,
    evidence_id: UUID,
    user: CurrentUser,
    service: EvidenceServiceDependency,
) -> EvidenceReadUrlView:
    try:
        return service.signed_read_url(user.id, case_id, evidence_id)
    except Exception as error:
        raise _translate_error(error) from error


@router.post("/{case_id}/demo-events/cctv-received", response_model=CaseView, status_code=201)
def receive_cctv(
    case_id: UUID,
    request: ReceiveCCTVRequest,
    user: CurrentUser,
    service: EvidenceServiceDependency,
) -> CaseView:
    try:
        return service.receive_cctv(user.id, case_id, request)
    except Exception as error:
        raise _translate_error(error) from error


@router.post("/{case_id}/demo-events/g1-media", response_model=CaseView, status_code=201)
def seed_g1_media(
    case_id: UUID,
    request: SeedG1MediaRequest,
    user: CurrentUser,
    service: EvidenceServiceDependency,
) -> CaseView:
    try:
        return service.seed_g1_media(user.id, case_id, request)
    except Exception as error:
        raise _translate_error(error) from error
