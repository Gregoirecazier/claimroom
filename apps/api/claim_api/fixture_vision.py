"""Opt-in scripted observations for exact synthetic media bytes.

These are visibly mock outputs, authored from media inspection. They are not
reference labels and must never be used as Vision evaluation results.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from claim_api.video_reuse import VideoObservation, VideoPipelineSpec
from claim_api.vision import ImageZone, UnsupportedFixtureError, VisualFact


PHOTO_HASHES = {
    "61ab6063a004314b849914a5e50bf632f6df11a7faebfd65135124e17fa73137": "g1_ensemble",
    "f4d31bd9e6100fd862c15ffd1afd35b3dbc7120ba4d07c3d40943218573dbc3f": "g1_detail",
    "2bd5a458c5b712d88accf2750d1a75fe92ef1aa4d5a3e174a0641fb0e7342aa5": "g2_ensemble",
    "b1d137905f3fe678c3b50253e703bd042efa0710c97716846d134bf3cb2d9b80": "g2_detail",
    "7f2b35c7916b2dbe8b122afb629bae8af319ebf1651f695208b637d196fbea08": "g3_ensemble",
    "414e45a7cab7faa766562b7eacbd8979078d650ed7253285d157d1c3b12da668": "g3_detail",
}
VIDEO_HASHES = {
    "a303991d762676b6cebaadc9faa5ae27118efd91bfcbb6c86cf438e276224904": "g1",
    "4f97e6c71532ec7e427a47d65764e5d46efc68940c89b1fcd48a1beb3936d6f5": "g2",
    "c528dea1f2fa78f2910325d46e67b090e75bca3ef7daaed422d203ccbba8d0af": "g3",
}


def _zone(x: float, y: float, width: float, height: float) -> ImageZone:
    return ImageZone(x=x, y=y, width=width, height=height)


def _photo_facts(name: str) -> list[VisualFact]:
    # Zones use image-relative coordinates. Photos show post-event condition
    # only; they are not independent evidence of movement or contact.
    if name == "g1_ensemble":
        return [
            VisualFact(category="damage", status="observed", text="Rear-left lamp and bumper of a silver car show visible deformation and dark marks.",
                       vehicle_track_id="silver-car", zone=_zone(.17, .38, .58, .29)),
            VisualFact(category="plate", status="observed", text="The plate on the silver car reads FR-482-KL.",
                       vehicle_track_id="silver-car", plate_candidate="FR-482-KL", zone=_zone(.47, .46, .28, .07)),
        ]
    if name == "g1_detail":
        return [VisualFact(category="damage", status="observed", text="Close view of a silver car shows deformation at the rear-left lamp and bumper.",
                           vehicle_track_id="silver-car", zone=_zone(.10, .20, .75, .57))]
    if name == "g2_ensemble":
        return [
            VisualFact(category="damage", status="observed", text="A silver car has visible deformation around its front-left wing and lamp.",
                       vehicle_track_id="silver-car", zone=_zone(.51, .33, .31, .38)),
            VisualFact(category="plate", status="observed", text="The plate on the silver car reads GH-271-RM.",
                       vehicle_track_id="silver-car", plate_candidate="GH-271-RM", zone=_zone(.13, .54, .29, .09)),
        ]
    if name == "g2_detail":
        return [VisualFact(category="damage", status="observed", text="Close view of a silver car shows a displaced front-left lamp area and scraped bumper.",
                           vehicle_track_id="silver-car", zone=_zone(.20, .20, .70, .60))]
    if name == "g3_ensemble":
        return [
            VisualFact(category="damage", status="observed", text="A red car shows visible deformation on a side door.",
                       vehicle_track_id="red-car", zone=_zone(.14, .31, .43, .29)),
            VisualFact(category="plate", status="observed", text="The plate on the red car reads LM21 RZT.",
                       vehicle_track_id="red-car", plate_candidate="LM21 RZT", zone=_zone(.74, .49, .22, .09)),
        ]
    return [VisualFact(category="damage", status="observed", text="Close view of a red car shows folds and light marks on a side door.",
                       vehicle_track_id="red-car", zone=_zone(.10, .18, .75, .60))]


def _video_facts(name: str) -> list[VideoObservation]:
    if name == "g1":
        return [
            VideoObservation(start_ms=200, end_ms=1000, category="vehicle", status="observed",
                             description="A stationary silver car is visible beside the curb.", vehicle_track_id="silver-car"),
            VideoObservation(start_ms=1200, end_ms=3200, category="movement", status="observed",
                             description="A dark car moves past the silver car; the silver car's rear-left area appears deformed later in the excerpt.",
                             vehicle_track_id="dark-car"),
            VideoObservation(start_ms=3200, end_ms=4200, category="visible_text", status="uncertain",
                             description="Characters on the moving dark car's rear plate are partly readable.",
                             vehicle_track_id="dark-car", plate_candidate="AB12 CD?", uncertain_positions=[7],
                             uncertainty="Final character is not reliable in this mock observation."),
        ]
    if name == "g2":
        return [
            VideoObservation(start_ms=1000, end_ms=4200, category="visible_text", status="observed",
                             description="The plate XY34 ZTR appears on a blue car in the lower part of the frame.",
                             vehicle_track_id="blue-car", plate_candidate="XY34 ZTR"),
            VideoObservation(start_ms=2200, end_ms=4200, category="movement", status="observed",
                             description="A dark car moves toward a silver car and the two appear in contact; the blue car remains a separate track.",
                             vehicle_track_id="dark-car"),
            VideoObservation(start_ms=3200, end_ms=4200, category="visible_text", status="uncertain",
                             description="The dark car's front plate is only partly readable.",
                             vehicle_track_id="dark-car", plate_candidate="RK18 L?P", uncertain_positions=[6],
                             uncertainty="One character remains ambiguous in the sampled frames."),
        ]
    return [
        VideoObservation(start_ms=200, end_ms=1200, category="vehicle", status="observed",
                         description="A red car is visible near the junction.", vehicle_track_id="red-car"),
        VideoObservation(start_ms=1200, end_ms=3400, category="movement", status="observed",
                         description="A light-coloured car moves from behind-left toward the side of the red car; the cars appear in contact by the end of this interval.",
                         vehicle_track_id="light-car"),
        VideoObservation(start_ms=3200, end_ms=4200, category="damage", status="observed",
                         description="The side of the red car and front-right area of the light car appear damaged after contact.",
                         vehicle_track_id="red-car"),
    ]


class FixtureMockAnalyzer:
    mode: Literal["mock"] = "mock"

    def analyze_image(self, image_path: Path, mime_type: str, pipeline: VideoPipelineSpec) -> list[VisualFact]:
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise UnsupportedFixtureError("Unsupported fixture image type.")
        digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
        name = PHOTO_HASHES.get(digest)
        if name is None:
            raise UnsupportedFixtureError("Media is not an approved synthetic fixture.")
        return _photo_facts(name)

    def analyze(self, video_path: Path, _pipeline: VideoPipelineSpec) -> tuple[list[VideoObservation], dict[str, Any]]:
        digest = hashlib.sha256(video_path.read_bytes()).hexdigest()
        name = VIDEO_HASHES.get(digest)
        if name is None:
            raise UnsupportedFixtureError("Media is not an approved synthetic fixture.")
        return _video_facts(name), {"cost_minor": 0}
