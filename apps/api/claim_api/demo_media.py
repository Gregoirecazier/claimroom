"""Private, versioned catalogue for the insured portal's synthetic media."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claim_api.g1_media import G1_MEDIA, G1_MEDIA_VERSION


MEDIA_ROOT = Path(__file__).resolve().parent / "fixture_media"


@dataclass(frozen=True)
class DemoMedia:
    filename: str
    kind: str
    mime_type: str
    role: str
    thumbnail_key: str
    subject: str
    provenance_note: str
    duration_seconds: float | None = None


@dataclass(frozen=True)
class DemoCatalogue:
    version: str
    items: tuple[DemoMedia, ...]


CATALOGUES = {
    "g1": DemoCatalogue(
        G1_MEDIA_VERSION,
        tuple(DemoMedia(item.filename, item.kind, item.mime_type, item.role,
                        item.filename if item.kind != "scene_video" else "photo-ensemble.png",
                        "Peugeot assurée", "Média synthétique de démonstration",
                        5.042 if item.kind == "scene_video" else None) for item in G1_MEDIA),
    ),
    "g2": DemoCatalogue("g2-media-v1", (
        DemoMedia("photo-renault-ensemble.png", "vehicle_photo", "image/png", "vue_ensemble",
                  "photo-renault-ensemble.png", "Renault assurée", "Vue synthétique reconstruite depuis la vidéo"),
        DemoMedia("photo-renault-detail.png", "damage_photo", "image/png", "detail_degats",
                  "photo-renault-detail.png", "Renault assurée", "Vue synthétique reconstruite depuis la vidéo"),
        DemoMedia("video-g2.mp4", "scene_video", "video/mp4", "video_g2",
                  "photo-renault-ensemble.png", "Scène G2", "Vidéo synthétique fournie", 5.042),
    )),
    "g3": DemoCatalogue("g3-media-v1", (
        DemoMedia("photo-toyota-ensemble.png", "vehicle_photo", "image/png", "vue_ensemble",
                  "photo-toyota-ensemble.png", "Toyota, véhicule tiers", "Vue synthétique reconstruite depuis la vidéo"),
        DemoMedia("photo-toyota-detail.png", "damage_photo", "image/png", "detail_degats",
                  "photo-toyota-detail.png", "Toyota, véhicule tiers", "Vue synthétique reconstruite depuis la vidéo"),
        DemoMedia("video-g3.mp4", "scene_video", "video/mp4", "video_g3",
                  "photo-toyota-ensemble.png", "Scène G3", "Vidéo synthétique fournie", 5.042),
    )),
}


def fixture(scenario_id: str, media_key: str) -> tuple[DemoCatalogue, DemoMedia, Path] | None:
    catalogue = CATALOGUES.get(scenario_id)
    if catalogue is None:
        return None
    item = next((candidate for candidate in catalogue.items if candidate.filename == media_key), None)
    if item is None:
        return None
    return catalogue, item, MEDIA_ROOT / scenario_id / item.filename
