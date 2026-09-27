from __future__ import annotations

from typing import Protocol
from uuid import UUID

from claim_api.fixtures import FIXTURE_VERSION, PROVIDER_FIXTURE_VERSION, ProviderFixture, get_scenario
from claim_api.models import (
    AttachQuoteRequest, CaseListItem, CaseListPage, CaseView, Intake, IntakePatch,
    RemoveQuoteRequest, ReportLineEditRequest, UpsertEstimateRequest,
)


class CaseRepository(Protocol):
    def create_case(
        self,
        actor_id: UUID,
        scenario_id: str,
        intake: Intake,
        provider_results: tuple[ProviderFixture, ...],
        scenario_version: str,
        provider_source_version: str,
    ) -> CaseView: ...

    def list_cases(self, actor_id: UUID, limit: int) -> list[CaseListItem]: ...

    def list_cases_page(self, actor_id: UUID, query: str, offset: int, limit: int) -> CaseListPage: ...

    def get_case(self, actor_id: UUID, case_id: UUID) -> CaseView | None: ...

    def delete_case(self, actor_id: UUID, case_id: UUID) -> bool: ...

    def update_intake(
        self,
        actor_id: UUID,
        case_id: UUID,
        expected_state_version: int,
        patch: IntakePatch,
    ) -> CaseView | None: ...

    def edit_report_line(self, actor_id: UUID, case_id: UUID, line_id: str,
                         request: ReportLineEditRequest) -> CaseView | None: ...

    def upsert_estimate(self, actor_id: UUID, case_id: UUID,
                        request: UpsertEstimateRequest) -> CaseView | None: ...

    def attach_quote(self, actor_id: UUID, case_id: UUID,
                     request: AttachQuoteRequest) -> CaseView | None: ...

    def remove_quote(self, actor_id: UUID, case_id: UUID,
                     request: RemoveQuoteRequest) -> CaseView | None: ...


class UnsupportedActorError(ValueError):
    """Raised if the API's authenticated subject is not a Supabase UUID."""


class CaseNotFoundError(LookupError):
    pass


class StaleCaseError(RuntimeError):
    def __init__(self, current_state_version: int) -> None:
        self.current_state_version = current_state_version
        super().__init__("The case changed; refresh before editing.")


class CaseReadOnlyError(RuntimeError):
    """A registered or sent case cannot receive new insured material."""


def merge_intake(current: Intake, patch: IntakePatch) -> Intake:
    changed = patch.model_dump(exclude_unset=True)
    if not changed:
        raise ValueError("At least one intake field must be changed.")
    merged = current.model_dump(mode="json")
    changed = {key: value.strip() if isinstance(value, str) else value for key, value in changed.items()}
    merged.update({key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in changed.items()})
    if "incident_at" in changed and changed["incident_at"] != current.incident_at:
        merged["time_source"] = "handler_entered" if changed["incident_at"] is not None else None
    if any(key in changed and changed[key] != getattr(current, key)
           for key in ("insured_name", "insured_reference", "insured_vehicle", "insured_plate")):
        merged["insured_identity_source"] = "handler_entered"
    # Preserve the existing required intake fields. A voice extraction label
    # does not create a new requirement unless it names an editable intake field.
    required = ["insured_name", "insured_reference", "incident_at", "location", "danger_status", "injury_status"]
    required.extend(name for name in current.missing_fields
                    if name in IntakePatch.model_fields and name not in required)
    merged["missing_fields"] = [name for name in required if merged.get(name) in (None, "")]
    updated = Intake.model_validate(merged)
    return current if updated == current else updated


def _actor_uuid(actor_id: str) -> UUID:
    try:
        return UUID(actor_id)
    except ValueError as exc:
        raise UnsupportedActorError("Case storage requires a Supabase user UUID.") from exc


class CaseService:
    def __init__(self, repository: CaseRepository) -> None:
        self.repository = repository

    def create_case(self, actor_id: str, scenario_id: str) -> CaseView:
        fixture = get_scenario(scenario_id)
        return self.repository.create_case(
            _actor_uuid(actor_id),
            fixture.scenario_id,
            fixture.intake,
            fixture.provider_results,
            FIXTURE_VERSION,
            PROVIDER_FIXTURE_VERSION,
        )

    def list_cases(self, actor_id: str, limit: int) -> list[CaseListItem]:
        return self.repository.list_cases(_actor_uuid(actor_id), limit)

    def list_cases_page(self, actor_id: str, query: str, offset: int, limit: int) -> CaseListPage:
        return self.repository.list_cases_page(_actor_uuid(actor_id), query.strip(), offset, limit)

    def get_case(self, actor_id: str, case_id: UUID) -> CaseView:
        case = self.repository.get_case(_actor_uuid(actor_id), case_id)
        if case is None:
            raise CaseNotFoundError
        return case

    def delete_case(self, actor_id: str, case_id: UUID) -> None:
        if not self.repository.delete_case(_actor_uuid(actor_id), case_id):
            raise CaseNotFoundError

    def update_intake(
        self,
        actor_id: str,
        case_id: UUID,
        expected_state_version: int,
        patch: IntakePatch,
    ) -> CaseView:
        updated = self.repository.update_intake(
            _actor_uuid(actor_id), case_id, expected_state_version, patch
        )
        if updated is None:
            raise CaseNotFoundError
        return updated

    def edit_report_line(self, actor_id: str, case_id: UUID, line_id: str,
                         request: ReportLineEditRequest) -> CaseView:
        updated = self.repository.edit_report_line(_actor_uuid(actor_id), case_id, line_id, request)
        if updated is None:
            raise CaseNotFoundError
        return updated

    def upsert_estimate(self, actor_id: str, case_id: UUID,
                        request: UpsertEstimateRequest) -> CaseView:
        updated = self.repository.upsert_estimate(_actor_uuid(actor_id), case_id, request)
        if updated is None:
            raise CaseNotFoundError
        return updated

    def attach_quote(self, actor_id: str, case_id: UUID,
                     request: AttachQuoteRequest) -> CaseView:
        updated = self.repository.attach_quote(_actor_uuid(actor_id), case_id, request)
        if updated is None:
            raise CaseNotFoundError
        return updated

    def remove_quote(self, actor_id: str, case_id: UUID,
                     request: RemoveQuoteRequest) -> CaseView:
        updated = self.repository.remove_quote(_actor_uuid(actor_id), case_id, request)
        if updated is None:
            raise CaseNotFoundError
        return updated
