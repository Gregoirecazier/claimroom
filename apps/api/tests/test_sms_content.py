from __future__ import annotations

from datetime import datetime, timezone

from claim_api.models import Intake
from claim_api.sms_content import compose_follow_up


URL = "https://claimroom.example/depot#token=opaque-test-token"


def _intake(**changes) -> Intake:
    values = {
        "reported_at": datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc),
        "incident_at": datetime(2026, 9, 25, 8, 45, tzinfo=timezone.utc),
        "time_source": "inferred_from_call",
        "narrative": "J'étais à l'arrêt quand un véhicule a touché l'arrière de ma voiture.",
        "insured_reference": "SIN-42",
        "location": "Paris",
        "injury_status": "no",
        "danger_status": "no",
        "insured_vehicle": "Peugeot",
        "insured_plate": "FR-482-KL",
        "policy_reference": None,
    }
    values.update(changes)
    return Intake.model_validate(values)


def test_follow_up_keeps_uncertain_time_and_only_missing_media() -> None:
    result = compose_follow_up(
        _intake(), evidence_kinds={"scene_photo", "damage_photo"},
        facts=[{"field": "third_party_involved", "value": "yes"}], deposit_url=URL,
    )
    assert "Heure estimée à partir de l'appel" in result.body
    assert "Heure déclarée" not in result.body
    assert "une vue large" not in result.body and "un gros plan" not in result.body
    assert "si disponible, une photo ou vidéo du tiers" in result.body
    assert "référence de votre contrat" in result.body
    assert result.body.endswith(URL)


def test_follow_up_marks_missing_time_without_inventing_third_party() -> None:
    result = compose_follow_up(
        _intake(policy_reference="POL-1", incident_at=None, time_source=None),
        evidence_kinds={"scene_video", "damage_photo"}, facts=[], deposit_url=URL,
    )
    assert "À compléter : date et heure de l'accident." in result.body
    assert "Heure" not in result.body
    assert "tiers et de sa plaque" not in result.body
    assert "Pièces utiles" not in result.body


def test_follow_up_shortens_long_narrative_without_adding_claims() -> None:
    result = compose_follow_up(
        _intake(narrative="Déclaration longue " * 30), evidence_kinds=set(), facts=[],
        deposit_url=URL, synthetic_demo=True,
    )
    assert result.warnings == ("summary_shortened",)
    assert "Une vidéo de démonstration" in result.body
    assert result.body.endswith(URL)


def test_follow_up_lists_missing_safety_and_incident_facts() -> None:
    result = compose_follow_up(
        _intake(insured_reference=None, location=None, incident_at=None,
                injury_status="unknown", danger_status=None),
        evidence_kinds=set(), facts=[], deposit_url=URL,
    )
    assert "référence de sinistre" in result.missing_items[0]
    assert "lieu de l'accident" in result.missing_items
    assert "date et heure de l'accident" in result.missing_items
    assert "présence éventuelle de blessés" in result.missing_items
    assert "danger actuel éventuel" in result.missing_items
