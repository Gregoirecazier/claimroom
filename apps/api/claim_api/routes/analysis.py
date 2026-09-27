from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from claim_api.analysis import (
    AnalysisInProgressError,
    AnalysisService,
    PipelexClaimsAnalyzer,
    UrgentHandoffError,
)
from claim_api.auth import CurrentUser
from claim_api.gemini_analysis import GeminiClaimsAnalyzer
from claim_api.hybrid_analysis import HybridClaimsAnalyzer
from claim_api.media_analysis import MediaAnalysisService
from claim_api.case_service import CaseNotFoundError, StaleCaseError, UnsupportedActorError
from claim_api.models import AnalysisRunRequest, AnalysisRunResponse
from claim_api.postgres_cases import (
    DatabaseConfigurationError,
    DatabaseUnavailableError,
    PostgresCaseRepository,
)


router = APIRouter(prefix="/v1/cases", tags=["analysis"])


def get_analysis_service() -> AnalysisService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(status_code=503, detail={
            "code": "database_not_configured", "message": "DATABASE_URL is required for case operations.", "details": {},
        })
    try:
        repository = PostgresCaseRepository(database_url)
    except DatabaseConfigurationError as error:
        raise HTTPException(status_code=503, detail={
            "code": "database_tls_required", "message": str(error), "details": {},
        }) from error
    provider = os.getenv("ANALYSIS_PROVIDER", "gemini").strip().lower()
    if provider not in {"gemini", "pipelex", "hybrid"}:
        raise HTTPException(status_code=503, detail={
            "code": "analysis_provider_invalid", "message": "ANALYSIS_PROVIDER must be gemini, pipelex, or hybrid.", "details": {},
        })
    analyzer = {
        "gemini": GeminiClaimsAnalyzer,
        "pipelex": PipelexClaimsAnalyzer,
        "hybrid": HybridClaimsAnalyzer,
    }[provider]()
    return AnalysisService(repository=repository, analyzer=analyzer)


AnalysisServiceDependency = Annotated[AnalysisService, Depends(get_analysis_service)]


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, CaseNotFoundError):
        return HTTPException(status_code=404, detail={"code": "not_found", "message": "Case not found.", "details": {}})
    if isinstance(error, UnsupportedActorError):
        return HTTPException(status_code=403, detail={"code": "invalid_actor", "message": str(error), "details": {}})
    if isinstance(error, StaleCaseError):
        return HTTPException(status_code=409, detail={
            "code": "stale_case", "message": "The case changed; refresh before analysis.",
            "details": {"current_state_version": error.current_state_version},
        })
    if isinstance(error, AnalysisInProgressError):
        return HTTPException(status_code=409, detail={"code": "analysis_in_progress", "message": str(error), "details": {}})
    if isinstance(error, UrgentHandoffError):
        return HTTPException(status_code=422, detail={"code": "urgent_human_handoff", "message": str(error), "details": {}})
    if isinstance(error, DatabaseUnavailableError):
        return HTTPException(status_code=503, detail={"code": "database_unavailable", "message": "Case storage is temporarily unavailable.", "details": {}})
    raise error


@router.post("/{case_id}/analysis-runs", response_model=AnalysisRunResponse)
def run_analysis(
    case_id: UUID,
    request: AnalysisRunRequest,
    user: CurrentUser,
    service: AnalysisServiceDependency,
) -> AnalysisRunResponse:
    try:
        return service.run(user.id, case_id, request)
    except Exception as error:
        raise _translate_error(error) from error


def get_media_analysis_service() -> MediaAnalysisService:
    # Reuse the authorized repository, but always select Gemini for this route.
    repository = get_analysis_service().repository
    def follow_up(actor_id,case):
        from claim_api.media_workflow import MediaWorkflow,MediaWorkflowRepository
        workflow = MediaWorkflow(MediaWorkflowRepository(repository.database_url))
        workflow.repository.enqueue(UUID(actor_id),case,retry=True)
        workflow.tick(case.id)
    return MediaAnalysisService(repository,on_ready=follow_up)


MediaAnalysisDependency = Annotated[MediaAnalysisService, Depends(get_media_analysis_service)]


@router.post("/{case_id}/media-analysis-runs", response_model=AnalysisRunResponse)
def run_media_analysis(case_id: UUID, request: AnalysisRunRequest,
                       user: CurrentUser, service: MediaAnalysisDependency) -> AnalysisRunResponse:
    try:
        return service.run(user.id, case_id, request)
    except Exception as error:
        raise _translate_error(error) from error
