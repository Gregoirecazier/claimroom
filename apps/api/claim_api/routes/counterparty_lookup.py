from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from claim_api.auth import CurrentUser
from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.counterparty_lookup import CounterpartyLookupRequest, CounterpartyLookupService, LookupRejectedError
from claim_api.models import CaseView
from claim_api.postgres_cases import DatabaseConfigurationError, DatabaseUnavailableError, PostgresCaseRepository

router = APIRouter(prefix="/v1/cases", tags=["counterparty lookup"])


def get_lookup_service() -> CounterpartyLookupService:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise HTTPException(503, detail={"code": "database_not_configured", "message": "DATABASE_URL is required.", "details": {}})
    try:
        return CounterpartyLookupService(PostgresCaseRepository(url))
    except DatabaseConfigurationError as error:
        raise HTTPException(503, detail={"code": "database_not_configured", "message": str(error), "details": {}}) from error


@router.post("/{case_id}/counterparty-lookup", response_model=CaseView)
def run_lookup(case_id: UUID, request: CounterpartyLookupRequest, user: CurrentUser,
               service: Annotated[CounterpartyLookupService, Depends(get_lookup_service)]) -> CaseView:
    try:
        return service.run(user.id, case_id, request)
    except CaseNotFoundError as error:
        raise HTTPException(404, detail={"code": "not_found", "message": "Case not found.", "details": {}}) from error
    except StaleCaseError as error:
        raise HTTPException(409, detail={"code": "stale_case", "message": str(error),
                                         "details": {"current_state_version": error.current_state_version}}) from error
    except LookupRejectedError as error:
        raise HTTPException(422, detail={"code": error.code, "message": str(error), "details": {}}) from error
    except DatabaseUnavailableError as error:
        raise HTTPException(503, detail={"code": "database_unavailable", "message": str(error), "details": {}}) from error
