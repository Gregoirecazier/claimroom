from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from claim_api.auth import CurrentUser
from claim_api.evidence_service import EvidenceNotFoundError
from claim_api.postgres_cases import DatabaseConfigurationError, DatabaseUnavailableError, PostgresCaseRepository
from claim_api.storage import StorageConfigurationError, SupabaseStorageAdapter
from claim_api.video_reuse import PostgresVideoRepository, VideoReuseService, configured_vision_pipeline
from claim_api.fixture_vision import FixtureMockAnalyzer
from claim_api.vision import (
    PostgresVisionRepository, VisionRequest, VisionResponse, VisionService,
    validate_video_observations,
)


router = APIRouter(prefix="/v1/cases", tags=["vision"])


def get_vision_service() -> VisionService:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(503, detail={"code": "database_not_configured", "message": "DATABASE_URL is required.", "details": {}})
    try:
        cases = PostgresCaseRepository(database_url)
        storage = SupabaseStorageAdapter.from_env()
        # A new output schema prevents legacy, unbounded S11 observations from
        # being silently accepted as source-validated S12 observations.
        pipeline = configured_vision_pipeline()
    except (DatabaseConfigurationError, StorageConfigurationError, ValueError) as error:
        raise HTTPException(503, detail={"code": "vision_not_configured", "message": str(error), "details": {}}) from error
    analyzer = FixtureMockAnalyzer() if os.getenv("VISION_FIXTURE_MOCK", "").strip() == "1" else None
    video = VideoReuseService(PostgresVideoRepository(cases), storage, pipeline, analyzer=analyzer,
                              observation_validator=validate_video_observations)
    return VisionService(PostgresVisionRepository(cases), storage, analyzer=analyzer,
                         video=video, pipeline=pipeline)


VisionDependency = Annotated[VisionService, Depends(get_vision_service)]


@router.post("/{case_id}/vision-observations", response_model=VisionResponse)
def run_vision(case_id: UUID, request: VisionRequest, user: CurrentUser,
               service: VisionDependency) -> VisionResponse:
    try:
        return service.run(user.id, case_id, request)
    except EvidenceNotFoundError as error:
        raise HTTPException(404, detail={"code": "not_found", "message": "Evidence not found.", "details": {}}) from error
    except ValueError as error:
        raise HTTPException(422, detail={"code": "invalid_evidence", "message": str(error), "details": {}}) from error
    except DatabaseUnavailableError as error:
        raise HTTPException(503, detail={"code": "database_unavailable", "message": "Case storage is unavailable.", "details": {}}) from error
