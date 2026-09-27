import hmac
import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException

from claim_api.auth import CurrentUser
from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.correspondence import (
    CorrespondenceService,
    CorrespondenceError,
    CameraMailRequest,
    EditCorrespondence,
    SendCorrespondence,
    ResendMailer,
)
from claim_api.media_workflow import MediaWorkflow, MediaWorkflowRepository
from claim_api.models import AnalysisRunRequest
from claim_api.postgres_cases import DatabaseUnavailableError

router = APIRouter(tags=["media workflow"])


def workflow():
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise HTTPException(503, "Workflow storage is not configured.")
    return MediaWorkflow(MediaWorkflowRepository(url))


Service = Annotated[MediaWorkflow, Depends(workflow)]


def translate(error):
    if isinstance(error, CaseNotFoundError):
        return HTTPException(404, "Case not found.")
    if isinstance(error, StaleCaseError):
        return HTTPException(409, "Dossier modifié : actualisez la page.")
    if isinstance(error, CorrespondenceError):
        return HTTPException(
            503 if error.code == "email_not_configured" else 409,
            detail={"code": error.code, "message": error.code, "details": {}},
        )
    if isinstance(error, DatabaseUnavailableError):
        return HTTPException(503, "Workflow storage unavailable.")
    raise error


@router.get("/v1/cases/{case_id}/automation")
def status(case_id: UUID, user: CurrentUser, service: Service):
    try:
        result = service.repository.snapshot(UUID(user.id), case_id)
        result["email_configured"] = ResendMailer().configured
        result["worker_configured"] = bool(os.getenv("MEDIA_WORKER_TOKEN", ""))
        return result
    except Exception as error:
        raise translate(error) from error


@router.post("/v1/cases/{case_id}/automation/advance")
def advance(case_id: UUID, user: CurrentUser, service: Service):
    # Browser fallback: processes queued analysis and notifications already authorized by handler approval.
    try:
        if not service.repository.get_case(UUID(user.id), case_id):
            raise CaseNotFoundError
        service.tick(case_id)
        return service.repository.snapshot(UUID(user.id), case_id)
    except Exception as error:
        raise translate(error) from error


@router.post("/internal/media-workflow/tick")
def tick(service: Service, authorization: Annotated[str | None, Header()] = None):
    token = os.getenv("MEDIA_WORKER_TOKEN", "")
    if len(token) < 32 or not hmac.compare_digest(
        authorization or "", f"Bearer {token}"
    ):
        raise HTTPException(401, "Invalid worker authentication.")
    return {"processed": service.tick()}


@router.post("/v1/cases/{case_id}/automation/retry")
def retry(
    case_id: UUID, request: AnalysisRunRequest, user: CurrentUser, service: Service
):
    try:
        actor = UUID(user.id)
        case = service.repository.get_case(actor, case_id)
        if case is None:
            raise CaseNotFoundError
        if case.state_version != request.expected_state_version:
            raise StaleCaseError(case.state_version)
        current = service.repository.snapshot(actor, case_id)["workflow"]
        if not current or current["status"] != "failed":
            raise HTTPException(409, "Seul un traitement échoué peut être relancé ici.")
        service.repository.enqueue(actor, case, retry=True)
        service.tick(case_id)
        return service.repository.snapshot(actor, case_id)
    except Exception as error:
        raise translate(error) from error


def correspondence():
    return CorrespondenceService(os.getenv("DATABASE_URL", ""))


Mail = Annotated[CorrespondenceService, Depends(correspondence)]


def public_draft(row):
    return {k: v for k, v in row.items() if k not in {"send_payload"}}


@router.post("/v1/cases/{case_id}/correspondence/cctv", status_code=201)
def create_camera_mail(
    case_id: UUID, request: CameraMailRequest, user: CurrentUser, service: Mail
):
    try:
        return public_draft(service.camera_draft(UUID(user.id), case_id, request))
    except Exception as error:
        raise translate(error) from error


@router.put("/v1/cases/{case_id}/correspondence/{draft_id}")
def edit_mail(
    case_id: UUID,
    draft_id: UUID,
    request: EditCorrespondence,
    user: CurrentUser,
    service: Mail,
):
    try:
        return public_draft(service.edit(UUID(user.id), case_id, draft_id, request))
    except Exception as error:
        raise translate(error) from error


@router.post("/v1/cases/{case_id}/correspondence/{draft_id}/send")
def send_mail(
    case_id: UUID,
    draft_id: UUID,
    request: SendCorrespondence,
    user: CurrentUser,
    service: Mail,
):
    try:
        return public_draft(service.send(UUID(user.id), case_id, draft_id, request))
    except Exception as error:
        raise translate(error) from error
