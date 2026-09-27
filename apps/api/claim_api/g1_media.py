from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class G1Media:
    filename: str
    kind: str
    mime_type: str
    role: str
    display_order: int


G1_MEDIA = (
    G1Media("photo-ensemble.png", "vehicle_photo", "image/png", "vue_ensemble", 1),
    G1Media("photo-detail.png", "damage_photo", "image/png", "detail_degats", 2),
    G1Media("video-g1.mp4", "scene_video", "video/mp4", "video_g1", 3),
)
G1_MEDIA_VERSION = "g1-media-v2"
G1_MEDIA_DIR = Path(__file__).resolve().parent / "fixture_media" / "g1"
