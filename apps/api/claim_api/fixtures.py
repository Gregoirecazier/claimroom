from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from claim_api.models import Intake

FIXTURE_VERSION = "synthetic-scenarios-v4"
PROVIDER_FIXTURE_VERSION = "lookup-fixtures-v1"


@dataclass(frozen=True)
class ProviderFixture:
    provider: str
    status: Literal["matched", "no_match", "ambiguous", "unavailable", "error"]
    query: dict[str, Any]
    data: dict[str, Any]
    reason: str | None = None


@dataclass(frozen=True)
class ScenarioFixture:
    scenario_id: str
    intake: Intake
    provider_results: tuple[ProviderFixture, ...]


def _intake(
    *,
    reported_at: str,
    insured_reference: str | None,
    insured_name: str | None = None,
    insured_vehicle: str | None = None,
    insured_plate: str | None = None,
    policy_reference: str | None,
    incident_at: str,
    time_source: Literal["caller_statement", "inferred_from_call"] = "caller_statement",
    location: str,
    vehicle_country: str,
    narrative: str,
    danger_status: Literal["yes", "no", "unknown"],
    injury_status: Literal["yes", "no", "unknown"],
) -> Intake:
    candidate = Intake(
        reported_at=datetime.fromisoformat(reported_at),
        insured_reference=insured_reference,
        insured_name=insured_name,
        insured_vehicle=insured_vehicle,
        insured_plate=insured_plate,
        insured_identity_source="synthetic_fixture" if any((insured_name, insured_vehicle, insured_plate)) else None,
        policy_reference=policy_reference,
        incident_at=datetime.fromisoformat(incident_at),
        time_source=time_source,
        location=location,
        vehicle_country=vehicle_country,
        narrative=narrative,
        danger_status=danger_status,
        injury_status=injury_status,
    )
    return candidate.model_copy(update={"missing_fields": missing_intake_fields(candidate)})


def missing_intake_fields(intake: Intake) -> list[str]:
    required = {
        "insured_name": intake.insured_name,
        "insured_reference": intake.insured_reference,
        "incident_at": intake.incident_at,
        "location": intake.location,
        "danger_status": intake.danger_status,
        "injury_status": intake.injury_status,
    }
    return [name for name, value in required.items() if value is None or value == ""]


SCENARIOS: dict[str, ScenarioFixture] = {
    "g1": ScenarioFixture(
        scenario_id="g1",
        intake=_intake(
            reported_at="2026-09-25T10:33:00+02:00",
            insured_reference="CLM-2026-0842",
            insured_name="Camille Martin",
            insured_vehicle="Peugeot grise",
            insured_plate="FR-482-KL",
            policy_reference=None,
            incident_at="2026-09-25T10:33:00+02:00",
            time_source="inferred_from_call",
            location="18 rue des Ateliers-Démo, Paris, France",
            vehicle_country="FR",
            narrative="Synthetic caller says her grey Peugeot was stationary when a black BMW struck its left rear and left. The third-party plate and collision dynamics remain unverified pending review of G1 video.",
            danger_status="no",
            injury_status="no",
        ),
        provider_results=(),
    ),
    "g2": ScenarioFixture(
        scenario_id="g2",
        intake=_intake(
            reported_at="2026-09-25T10:33:00+02:00",
            insured_reference="CLM-2026-G2",
            insured_vehicle="Renault Mégane grise",
            insured_plate="GH-271-RM",
            policy_reference=None,
            incident_at="2026-09-25T10:33:00+02:00",
            location="Paris, France",
            vehicle_country="FR",
            narrative="L'assuré déclare un choc avec une voiture noire. La plaque du tiers reste à confirmer.",
            danger_status="no",
            injury_status="no",
        ),
        provider_results=(),
    ),
    "g3": ScenarioFixture(
        scenario_id="g3",
        intake=_intake(
            reported_at="2026-09-25T10:33:00+02:00",
            insured_reference="CLM-2026-G3",
            insured_vehicle="Citroën C3 gris clair",
            insured_plate="GT-638-VN",
            policy_reference=None,
            incident_at="2026-09-25T10:33:00+02:00",
            location="Paris, France",
            vehicle_country="FR",
            narrative="L'assuré déclare que sa Citroën était à l'arrêt lors du choc avec une Toyota.",
            danger_status="no",
            injury_status="no",
        ),
        provider_results=(),
    ),
    "complete": ScenarioFixture(
        scenario_id="complete",
        intake=_intake(
            reported_at="2025-06-14T18:01:00+02:00",
            insured_reference="CLAIM-SYN-1042",
            policy_reference="POLICY-FR-SYN-88210",
            incident_at="2025-06-14T17:32:00+02:00",
            location="Rue Faidherbe, Lille, France",
            vehicle_country="FR",
            narrative="French insured reports their car was struck in a hit-and-run by a UK-registered vehicle. The caller clearly read the unique plate UK-SYN-482 before the driver left.",
            danger_status="no",
            injury_status="no",
        ),
        provider_results=(
            ProviderFixture(
                provider="insurance_lookup",
                status="matched",
                query={"plate_candidate": "UK-SYN-482", "incident_date": "2025-06-14"},
                data={
                    "coverage": "matched",
                    "insurer_name": "Britannia Demo Motor (fictional)",
                    "vehicle_country": "GB",
                    "insurer_country": "GB",
                    "correspondent_country": "FR",
                    "policy_reference": "POLICY-UK-SYN-482",
                    "valid_on_incident_date": True,
                },
            ),
            ProviderFixture(
                provider="correspondent_lookup",
                status="matched",
                query={"insurer": "Britannia Demo Motor (fictional)", "insurer_country": "GB", "country": "FR"},
                data={
                    "correspondent_name": "Bureau Français Demo Claims Desk (fictional)",
                    "country": "FR",
                    "directory_reference": "FIX-CORR-001",
                },
            ),
        ),
    ),
    "ambiguous": ScenarioFixture(
        scenario_id="ambiguous",
        intake=_intake(
            reported_at="2025-08-02T10:02:00+02:00",
            insured_reference="CLAIM-SYN-2057",
            policy_reference=None,
            incident_at="2025-08-02T09:10:00+02:00",
            location="Place de la Gare, Luxembourg City",
            vehicle_country="BE",
            narrative="Synthetic caller reports a parked vehicle was struck; plate details are unclear.",
            danger_status="unknown",
            injury_status="unknown",
        ),
        provider_results=(
            ProviderFixture(
                provider="insurance_lookup",
                status="ambiguous",
                query={"plate_candidates": ["SYN-20?", "SYN-2O7"], "incident_date": "2025-08-02"},
                data={"candidate_count": 2, "coverage": "not_established"},
                reason="The fictional plate candidates do not identify one policy row.",
            ),
            ProviderFixture(
                provider="correspondent_lookup",
                status="unavailable",
                query={"insurer": None, "country": "LU"},
                data={"candidate_count": 0},
                reason="No correspondent search was completed because coverage is ambiguous.",
            ),
        ),
    ),
}


class UnknownScenarioError(ValueError):
    def __init__(self, scenario_id: str) -> None:
        self.scenario_id = scenario_id
        super().__init__(scenario_id)


def get_scenario(scenario_id: str) -> ScenarioFixture:
    try:
        return SCENARIOS[scenario_id]
    except KeyError as exc:
        raise UnknownScenarioError(scenario_id) from exc
