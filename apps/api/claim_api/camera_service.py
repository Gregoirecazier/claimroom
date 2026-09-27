"""S13 camera discovery and local, simulated request workflow."""

from __future__ import annotations

from uuid import UUID

from claim_api.camera_locator import CameraLocator, MockCameraLocator
from claim_api.case_service import CaseNotFoundError, _actor_uuid
from claim_api.models import (
    CameraMapView, CameraRequestTransition, CameraSearchView, CaseView,
    CreateCameraRequest, UpdateCameraRequestStatus,
)
from claim_api.camerci_cameras import CamerciCameraLocator
from claim_api.postgres_cases import PostgresCaseRepository


class CameraService:
    def __init__(self, repository: PostgresCaseRepository,
                 locator: CameraLocator | None = None) -> None:
        self.repository = repository
        self.locator = locator or MockCameraLocator()

    def search(self, actor_id: str, case_id: UUID) -> CameraSearchView:
        case = self.repository.get_case(_actor_uuid(actor_id), case_id)
        if case is None:
            raise CaseNotFoundError
        return self.locator.search(case)

    def map(self, actor_id: str, case_id: UUID, radius_m: int = 150) -> CameraMapView:
        case = self.repository.get_case(_actor_uuid(actor_id), case_id)
        if case is None:
            raise CaseNotFoundError
        if case.scenario_id == "g1" and case.intake.location == "18 rue des Ateliers-Démo, Paris, France":
            return CameraMapView(status="unresolved", address=case.intake.location, radius_m=radius_m,
                                 reason="Cette adresse de démonstration est fictive. Indiquez une adresse réelle pour afficher la carte.")
        return CamerciCameraLocator().search(case.intake.location, radius_m=radius_m)

    def create_draft(self, actor_id: str, case_id: UUID,
                     request: CreateCameraRequest) -> CaseView:
        actor = _actor_uuid(actor_id)
        case = self.repository.get_case(actor, case_id)
        if case is None:
            raise CaseNotFoundError
        if any(event.event_type == "case.cctv_received" for event in case.timeline):
            raise ValueError("The synthetic CCTV fixture has already been received for this case.")
        candidate = next((item for item in self.locator.search(case).candidates
                          if item.id == request.candidate_id), None)
        if candidate is None:
            raise ValueError("This camera candidate is unavailable for this intake.")
        scope, reason = request.scope.strip(), request.reason.strip()
        if len(scope) < 10 or len(reason) < 10:
            raise ValueError("Scope and reason must each contain at least ten characters.")
        result = self.repository.create_camera_request(
            actor, case_id, candidate, scope, reason, request.expected_state_version,
        )
        if result is None:
            raise CaseNotFoundError
        return result

    def approve(self, actor_id: str, case_id: UUID, request_id: UUID,
                request: CameraRequestTransition) -> CaseView:
        case = self.repository.get_case(_actor_uuid(actor_id), case_id)
        if case is None:
            raise CaseNotFoundError
        draft = next((item for item in case.camera_requests if item.id == request_id), None)
        if draft is not None and draft.status == "requested":
            return case
        if draft is None or draft.candidate_id not in {
            item.id for item in self.locator.search(case).candidates
        }:
            raise ValueError("The camera candidate no longer matches the current intake.")
        result = self.repository.change_camera_request(
            _actor_uuid(actor_id), case_id, request_id,
            request.expected_state_version, "requested",
        )
        if result is None:
            raise CaseNotFoundError
        return result

    def set_status(self, actor_id: str, case_id: UUID, request_id: UUID,
                   request: UpdateCameraRequestStatus) -> CaseView:
        result = self.repository.change_camera_request(
            _actor_uuid(actor_id), case_id, request_id,
            request.expected_state_version, request.status,
        )
        if result is None:
            raise CaseNotFoundError
        return result
