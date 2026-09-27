from __future__ import annotations

from datetime import datetime, timezone

import pytest

from claim_api.case_service import merge_intake
from claim_api.fixtures import SCENARIOS
from claim_api.models import IntakePatch
from claim_api.postgres_cases import DatabaseConfigurationError, validate_database_url


def test_scenario_fixtures_are_stable_and_visibly_mocked() -> None:
    complete = SCENARIOS["complete"]
    ambiguous = SCENARIOS["ambiguous"]

    assert complete.intake.reported_at == datetime(2025, 6, 14, 16, 1, tzinfo=timezone.utc)
    assert "French insured" in complete.intake.narrative
    assert "UK-registered" in complete.intake.narrative
    assert complete.provider_results[0].status == "matched"
    assert complete.provider_results[1].query["country"] == "FR"
    assert ambiguous.provider_results[0].status == "ambiguous"
    assert ambiguous.provider_results[0].data["coverage"] == "not_established"


def test_intake_merge_recomputes_missing_fields_and_normalizes_utc() -> None:
    current = SCENARIOS["complete"].intake
    updated = merge_intake(
        current,
        IntakePatch(location=None, incident_at=datetime.fromisoformat("2025-06-14T17:32:00+02:00")),
    )

    assert updated.incident_at == datetime(2025, 6, 14, 15, 32, tzinfo=timezone.utc)
    assert "location" in updated.missing_fields
    assert updated.reported_at == current.reported_at


def test_intake_merge_requires_nonempty_patch_and_timezone_aware_dates() -> None:
    current = SCENARIOS["complete"].intake

    with pytest.raises(ValueError, match="At least one"):
        merge_intake(current, IntakePatch())
    with pytest.raises(ValueError, match="timezone"):
        IntakePatch(incident_at=datetime(2025, 6, 14, 17, 32))


def test_intake_merge_accepts_vehicle_details_collected_after_voice_call() -> None:
    current = SCENARIOS["complete"].intake.model_copy(update={"insured_vehicle": None, "insured_plate": None})
    updated = merge_intake(current, IntakePatch(insured_vehicle="Peugeot 208", insured_plate="FR-123-AA"))

    assert updated.insured_vehicle == "Peugeot 208"
    assert updated.insured_plate == "FR-123-AA"
    assert updated.insured_reference == current.insured_reference


def test_database_url_requires_tls_except_explicit_loopback_development() -> None:
    secure = "postgresql://api:secret@db.example.test:6543/claims?sslmode=require"
    assert validate_database_url(secure, {"APP_ENV": "production"}) == secure

    with pytest.raises(DatabaseConfigurationError, match="TLS is required"):
        validate_database_url("postgresql://api:secret@db.example.test/claims")
    with pytest.raises(DatabaseConfigurationError, match="TLS is required"):
        validate_database_url(
            "postgresql://api:secret@127.0.0.1/claims",
            {"APP_ENV": "development", "DATABASE_ALLOW_INSECURE_LOCAL": "false"},
        )
    assert validate_database_url(
        "postgresql://api:secret@127.0.0.1/claims",
        {"APP_ENV": "development", "DATABASE_ALLOW_INSECURE_LOCAL": "true"},
    ).endswith("?sslmode=disable")
    with pytest.raises(DatabaseConfigurationError, match="TLS is required"):
        validate_database_url(
            "postgresql://api:secret@127.0.0.1/claims",
            {"APP_ENV": "development", "DATABASE_ALLOW_INSECURE_LOCAL": "true", "VERCEL": "1"},
        )
