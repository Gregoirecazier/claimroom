"""Read-only Camérci catalogue lookup around a geocoded claim address."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from claim_api.models import CameraMapView, MappedCameraView


RADIUS_M = 150
MAX_RADIUS_M = 500
_USER_AGENT = "ClaimRoom/0.1 (+https://github.com/Gregoirecazier/claim-subrogation-car-acceident)"
_GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
_CAMERCI_DATA = "https://camerci.fr/data/"
_lock = threading.Lock()
_last_geocode_request = 0.0
_cache: dict[str, tuple[float, CameraMapView]] = {}
_catalog: tuple[float, list["CameraEntry"], set[str]] | None = None


@dataclass(frozen=True)
class CameraEntry:
    id: str
    latitude: float
    longitude: float
    label: str
    operator: str | None


def _text_request(url: str) -> str:
    with urlopen(Request(url, headers={"User-Agent": _USER_AGENT}), timeout=8) as response:
        return response.read().decode("utf-8-sig")


def _geocode(address: str) -> object:
    url = f"{_GEOCODE_URL}?{urlencode({'q': address, 'format': 'jsonv2', 'addressdetails': 1, 'limit': 5})}"
    return json.loads(_text_request(url))


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    lat_a, lat_b = math.radians(lat1), math.radians(lat2)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(lon2 - lon1)
    term = math.sin(delta_lat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    return round(12_742_000 * math.asin(math.sqrt(term)))


def _load_catalog() -> tuple[list[CameraEntry], set[str]]:
    global _catalog
    with _lock:
        if _catalog and _catalog[0] > time.monotonic():
            return _catalog[1], _catalog[2]
        index = csv.DictReader(io.StringIO(_text_request(f"{_CAMERCI_DATA}index.csv")))
        cameras: list[CameraEntry] = []
        cities: set[str] = set()
        for source in index:
            slug = (source.get("CSV") or "").strip()
            if not re.fullmatch(r"[a-z0-9_-]+", slug):
                continue
            cities.add(slug)
            operator = (source.get("NOM") or "").strip() or None
            rows = csv.DictReader(io.StringIO(_text_request(f"{_CAMERCI_DATA}{slug}.csv")))
            for number, row in enumerate(rows, 1):
                try:
                    longitude = float(row["X"])
                    latitude = float(row["Y"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                    continue
                cameras.append(CameraEntry(
                    id=f"{slug}/{number}", latitude=latitude, longitude=longitude,
                    label=(row.get("NOM") or "").strip() or f"Caméra {number}", operator=operator,
                ))
        if not cameras:
            raise ValueError("Camérci catalogue is empty")
        _catalog = (time.monotonic() + 86400, cameras, cities)
        return cameras, cities


class CamerciCameraLocator:
    def search(self, address: str | None, radius_m: int = RADIUS_M) -> CameraMapView:
        if not 1 <= radius_m <= MAX_RADIUS_M:
            raise ValueError("Camera radius must be between 1 and 500 m")
        address = (address or "").strip()
        if not address:
            return CameraMapView(status="unresolved", radius_m=radius_m,
                                 reason="Renseignez une adresse précise du sinistre pour afficher la carte.")
        key = f"{radius_m}:{address.casefold()}"
        with _lock:
            cached = _cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
        try:
            global _last_geocode_request
            with _lock:
                delay = 1.0 - (time.monotonic() - _last_geocode_request)
                if delay > 0:
                    time.sleep(delay)
                _last_geocode_request = time.monotonic()
                matches = _geocode(address)
            if not isinstance(matches, list) or not matches:
                result = CameraMapView(status="unresolved", address=address, radius_m=radius_m,
                                       reason="Adresse introuvable sur OpenStreetMap. Précisez la rue et la ville.")
            else:
                match = matches[0]
                if not isinstance(match, dict) or match.get("addresstype") in {
                    "city", "town", "village", "municipality", "county", "state", "country",
                }:
                    result = CameraMapView(status="unresolved", address=address, radius_m=radius_m,
                                           reason="Le lieu est trop large. Indiquez une rue ou une adresse précise.")
                else:
                    latitude, longitude = float(match["lat"]), float(match["lon"])
                    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                        raise ValueError("Invalid geocoding coordinates")
                    entries, cities = _load_catalog()
                    nearby = [(entry, _distance_m(latitude, longitude, entry.latitude, entry.longitude))
                              for entry in entries]
                    nearby.sort(key=lambda item: (item[1], item[0].id))
                    selected = [item for item in nearby if item[1] <= radius_m]
                    outside_radius = not selected
                    if outside_radius:
                        selected = nearby[:1]
                    cameras = [MappedCameraView(
                        id=entry.id, latitude=entry.latitude, longitude=entry.longitude,
                        distance_m=distance, label=entry.label, operator=entry.operator,
                        camera_type=None,
                        source_url=f"https://camerci.fr/#17/{entry.latitude:.6f}/{entry.longitude:.6f}",
                    ) for entry, distance in selected]
                    address_parts = match.get("address") or {}
                    place = (address_parts.get("city") or address_parts.get("town")
                             or address_parts.get("municipality") or "").casefold()
                    covered = place in cities if place else bool(nearby and nearby[0][1] <= 10_000)
                    status = "not_covered" if outside_radius and not covered else "available"
                    reason = None
                    if outside_radius:
                        prefix = (f"Aucune caméra répertoriée par Camérci à {radius_m} m de cette adresse."
                                  if covered else "Camérci ne couvre pas encore ce secteur dans son catalogue public.")
                        reason = f"{prefix} La caméra la plus proche du catalogue est affichée hors du rayon de recherche."
                    result = CameraMapView(
                        status=status, address=address, resolved_address=match.get("display_name"),
                        latitude=latitude, longitude=longitude, radius_m=radius_m,
                        cameras=cameras, reason=reason,
                    )
        except (OSError, TimeoutError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return CameraMapView(status="unavailable", address=address, radius_m=radius_m,
                                 reason="Le catalogue Camérci ou le géocodage est momentanément indisponible.")
        with _lock:
            _cache[key] = (time.monotonic() + (3600 if result.status != "unresolved" else 86400), result)
            if len(_cache) > 256:
                _cache.pop(next(iter(_cache)))
        return result
