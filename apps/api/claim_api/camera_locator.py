"""Camera discovery port and an explicitly synthetic S13 scenario fixture."""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from claim_api.cctv_fixture import CCTV_FIXTURE_EVENT_ID
from claim_api.fixtures import SCENARIOS
from claim_api.models import CameraCandidateView, CameraSearchView, CaseView


CAMERA_SOURCE_VERSION = "synthetic-lille-camera-v1"
CAMERA_CANDIDATE_ID = "synthetic-lille-faidherbe-01"


class CameraLocator(Protocol):
    def search(self, case: CaseView) -> CameraSearchView: ...


class MockCameraLocator:
    """Only the exact Lille scenario has a camera; no geocoding is implied."""

    def search(self, case: CaseView) -> CameraSearchView:
        intake = case.intake
        complete = SCENARIOS["complete"].intake
        if (case.scenario_id != "complete" or intake.location != complete.location
                or intake.incident_at != complete.incident_at):
            return CameraSearchView(
                status="unavailable", source_version=CAMERA_SOURCE_VERSION,
                reason="Aucune caméra de démonstration n'est liée à ce lieu et à ce créneau."
            )
        incident = complete.incident_at
        assert incident is not None
        return CameraSearchView(
            status="candidates", source_version=CAMERA_SOURCE_VERSION,
            candidates=[CameraCandidateView(
                id=CAMERA_CANDIDATE_ID,
                label="Caméra de démonstration, rue Faidherbe",
                source=f"urn:claimroom:fixture:{CAMERA_SOURCE_VERSION}",
                controller="Responsable vidéo fictif — Lille",
                recipient="cctv@example.invalid",
                likely_from=incident - timedelta(minutes=5),
                likely_to=incident + timedelta(minutes=5),
                status="candidate", fixture_event_id=CCTV_FIXTURE_EVENT_ID,
            )],
        )
