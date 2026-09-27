from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from claim_api.auth import CurrentUser
from claim_api.evidence_service import EvidenceNotFoundError
from claim_api.postgres_cases import DatabaseConfigurationError, DatabaseUnavailableError, PostgresCaseRepository
from claim_api.storage import StorageAdapterError, StorageConfigurationError, StorageUnavailableError, SupabaseStorageAdapter
from claim_api.video_reuse import (
    PostgresVideoRepository, VideoAnalysisRequest, VideoAnalysisResponse,
    VideoAnalyzerUnavailable, VideoChecksumMismatch, configured_pipeline,
    VideoReuseService, VideoSourceChanged,
)


router = APIRouter(prefix="/v1/cases", tags=["video"])


def get_video_reuse_service() -> VideoReuseService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(503, detail={"code": "database_not_configured", "message": "DATABASE_URL is required.", "details": {}})
    try:
        cases = PostgresCaseRepository(database_url)
        storage = SupabaseStorageAdapter.from_env()
        pipeline = configured_pipeline()
    except (DatabaseConfigurationError, StorageConfigurationError, ValueError) as error:
        raise HTTPException(503, detail={"code": "video_not_configured", "message": str(error), "details": {}}) from error
    # A paid analyzer must be injected by the deployment once approved. The
    # default API can consume already-prepared artifacts without provider keys.
    return VideoReuseService(PostgresVideoRepository(cases), storage, pipeline)


VideoServiceDependency = Annotated[VideoReuseService, Depends(get_video_reuse_service)]


@router.post("/{case_id}/evidence/{evidence_id}/video-analysis", response_model=VideoAnalysisResponse)
def video_analysis(case_id: UUID, evidence_id: UUID, request: VideoAnalysisRequest,
                   user: CurrentUser, service: VideoServiceDependency) -> VideoAnalysisResponse:
    try:
        return service.run(user.id, case_id, evidence_id, request)
    except EvidenceNotFoundError as error:
        raise HTTPException(404, detail={"code": "not_found", "message": "Video evidence not found.", "details": {}}) from error
    except (VideoChecksumMismatch, VideoSourceChanged, ValueError) as error:
        raise HTTPException(422, detail={"code": "video_invalid", "message": str(error), "details": {}}) from error
    except VideoAnalyzerUnavailable as error:
        raise HTTPException(503, detail={"code": "analyzer_unavailable", "message": str(error), "details": {}}) from error
    except (DatabaseUnavailableError, StorageUnavailableError, StorageAdapterError) as error:
        raise HTTPException(503, detail={"code": "video_dependency_unavailable", "message": "Video storage or database unavailable.", "details": {}}) from error
