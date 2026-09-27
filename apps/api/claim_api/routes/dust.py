from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from claim_api.auth import CurrentUser
from claim_api.dust_client import DustClient, DustError
from claim_api.dust_models import DustRunRequest, DustRunView
from claim_api.dust_repository import PostgresDustRepository
from claim_api.dust_service import DustService
from claim_api.routes.cases import CaseServiceDependency
from claim_api.routes.analysis import _translate_error

router = APIRouter(prefix="/v1/cases", tags=["dust"])


def get_dust_service(cases: CaseServiceDependency) -> DustService:
    return DustService(
        PostgresDustRepository(cases.repository.database_url), DustClient.from_env()
    )


DustDependency = Annotated[DustService, Depends(get_dust_service)]


def translate(error):
    if isinstance(error, DustError):
        conflict = {
            "dust_idempotency_conflict",
            "dust_agent_in_progress",
            "dust_current_gemini_analysis_required",
            "dust_cctv_location_time_required",
            "dust_human_triage_required",
            "dust_synthetic_case_required",
        }
        messages = {
            "dust_current_gemini_analysis_required": "Run Gemini media analysis for the current case revision before repair costing.",
            "dust_not_configured": "Configure the server-side Dust key, workspace, region and agent IDs.",
            "dust_idempotency_conflict": "This idempotency key was used with a different request.",
            "dust_agent_in_progress": "This specialist already has an active run for this case.",
            "dust_cctv_location_time_required": "CCTV research requires the incident location and time.",
            "dust_human_triage_required": "Reported danger or injury requires human triage first.",
            "dust_synthetic_case_required": "This integration is currently limited to synthetic demo cases.",
        }
        return HTTPException(
            status_code=409 if error.code in conflict else 503,
            detail={
                "code": error.code,
                "message": messages.get(
                    error.code,
                    "Dust could not complete this operation. Check its configuration or refresh later.",
                ),
                "details": {},
            },
        )
    return _translate_error(error)


@router.post("/{case_id}/dust-runs", response_model=DustRunView, status_code=202)
def start_dust(
    case_id: UUID, request: DustRunRequest, user: CurrentUser, service: DustDependency
):
    try:
        return service.start(user.id, case_id, request)
    except Exception as error:
        raise translate(error) from error


@router.get("/{case_id}/dust-runs", response_model=list[DustRunView])
def list_dust(case_id: UUID, user: CurrentUser, service: DustDependency):
    try:
        return service.list_runs(user.id, case_id)
    except Exception as error:
        raise translate(error) from error


@router.post("/{case_id}/dust-runs/{run_id}/refresh", response_model=DustRunView)
def refresh_dust(
    case_id: UUID, run_id: UUID, user: CurrentUser, service: DustDependency
):
    try:
        return service.refresh(user.id, case_id, run_id)
    except Exception as error:
        raise translate(error) from error
