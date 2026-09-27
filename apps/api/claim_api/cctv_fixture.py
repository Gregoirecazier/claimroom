"""Deterministic, visibly synthetic CCTV still and observation fixture."""

from __future__ import annotations

import struct
import zlib


CCTV_FIXTURE_EVENT_ID = "cctv-lille-1732-v1"
CCTV_FIXTURE_VERSION = "synthetic-cctv-lille-v1"
MOCK_VISION_VERSION = "synthetic-visual-observations-v1"


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def synthetic_cctv_png() -> bytes:
    """Draw a small pixel-art street frame without runtime image dependencies."""
    width, height = 480, 270
    pixels = bytearray(width * height * 3)

    def put(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes(color)

    def rect(x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int]) -> None:
        for y in range(max(0, y1), min(height, y2)):
            for x in range(max(0, x1), min(width, x2)):
                put(x, y, color)

    def ellipse(cx: int, cy: int, rx: int, ry: int, color: tuple[int, int, int]) -> None:
        for y in range(max(0, cy - ry), min(height, cy + ry + 1)):
            for x in range(max(0, cx - rx), min(width, cx + rx + 1)):
                if ((x - cx) / max(rx, 1)) ** 2 + ((y - cy) / max(ry, 1)) ** 2 <= 1:
                    put(x, y, color)

    # Evening sky with a flat city block and a foreground road.
    for y in range(height):
        if y < 155:
            shade = max(0, 28 - y // 18)
            color = (30 + shade, 49 + shade, 56 + shade)
        elif y < 184:
            color = (73, 82, 76)
        else:
            color = (39, 45, 43)
        rect(0, y, width, y + 1, color)
    rect(0, 147, width, 155, (98, 103, 83))

    # Simplified building silhouettes and lit windows.
    buildings = [(0, 68, 83, 151), (76, 47, 161, 151), (156, 81, 251, 151), (245, 56, 351, 151), (343, 72, 480, 151)]
    for x1, y1, x2, y2 in buildings:
        rect(x1, y1, x2, y2, (35, 43, 43))
        rect(x1, y1, x2, y1 + 4, (53, 60, 55))
        for wx in range(x1 + 12, x2 - 7, 19):
            for wy in range(y1 + 15, y2 - 8, 23):
                tint = (104, 111, 83) if (wx + wy) % 3 else (69, 91, 88)
                rect(wx, wy, wx + 7, wy + 10, tint)

    # Road markings and a cross-street edge.
    rect(0, 183, width, 187, (111, 111, 94))
    for x in range(30, width, 95):
        rect(x, 226, x + 39, 229, (134, 132, 108))
    for x in range(28, 182, 27):
        rect(x, 186, x + 12, 207, (172, 168, 137))

    # Blurred-looking dark hatchback silhouette. Plate block contains no text.
    rect(217, 176, 348, 209, (26, 31, 32))
    rect(239, 164, 322, 182, (31, 38, 39))
    rect(248, 167, 277, 179, (72, 85, 83))
    rect(281, 167, 315, 179, (68, 80, 80))
    rect(337, 183, 350, 194, (153, 75, 55))
    rect(215, 185, 222, 194, (202, 152, 90))
    ellipse(242, 207, 13, 12, (17, 20, 20))
    ellipse(242, 207, 6, 6, (94, 99, 91))
    ellipse(321, 207, 13, 12, (17, 20, 20))
    ellipse(321, 207, 6, 6, (94, 99, 91))
    rect(290, 190, 310, 196, (132, 130, 113))
    rect(296, 192, 305, 194, (177, 175, 151))

    # Horizontal scan-lines make the frame read as a low-resolution fixture.
    for y in range(0, height, 3):
        for x in range(width):
            offset = (y * width + x) * 3
            pixels[offset] = pixels[offset] * 94 // 100
            pixels[offset + 1] = pixels[offset + 1] * 94 // 100
            pixels[offset + 2] = pixels[offset + 2] * 94 // 100

    raw = b"".join(b"\0" + pixels[y * width * 3 : (y + 1) * width * 3] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", zlib.compress(raw, 9)) + _png_chunk(b"IEND", b"")


def synthetic_observations(evidence_id: str) -> dict[str, object]:
    return {
        "observations": [
            {
                "text": "A small dark hatchback is visible crossing the frame; vehicle identity is not established.",
                "kind": "visual_observation",
                "assessment": "supported",
                "source_refs": [{"type": "evidence", "id": evidence_id, "locator": "frame:00:00:02.400"}],
            },
            {
                "text": "The registration plate is unreadable in this synthetic still.",
                "kind": "visual_observation",
                "assessment": "supported",
                "source_refs": [{"type": "evidence", "id": evidence_id, "locator": "region:plate"}],
            },
        ],
        "fixture_version": MOCK_VISION_VERSION,
    }
