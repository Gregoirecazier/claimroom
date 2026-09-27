from datetime import datetime, timezone
from uuid import uuid4

from claim_api.camera_locator import MockCameraLocator
from claim_api.fixtures import SCENARIOS
from claim_api.models import CaseView


def _case(scenario: str) -> CaseView:
    now = datetime.now(timezone.utc)
    return CaseView(
        id=uuid4(), created_by_user_id=uuid4(), scenario_id=scenario,
        synthetic=True, status="collecting", state_version=1, content_revision=1,
        created_at=now, updated_at=now, intake=SCENARIOS[scenario].intake.model_copy(deep=True),
    )


def test_locator_only_exposes_exact_lille_fixture_and_never_claims_real_coverage() -> None:
    locator = MockCameraLocator()
    result = locator.search(_case("complete"))
    assert result.status == "candidates" and result.mode == "mock"
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source.startswith("urn:claimroom:fixture:")
    assert candidate.recipient.endswith(".invalid")
    assert candidate.likely_from < SCENARIOS["complete"].intake.incident_at < candidate.likely_to

    assert locator.search(_case("g1")).status == "unavailable"
    moved = _case("complete")
    moved.intake.location = "Autre lieu"
    assert locator.search(moved).candidates == []
