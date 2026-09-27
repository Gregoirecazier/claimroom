"""OSM garage lookup and deterministic SMS composition. No message is sent here."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import OrderedDict
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import Field, ValidationError

from claim_api.models import ContractModel

RADII = (500, 1000, 2000, 5000)
ATTRIBUTION = "© OpenStreetMap contributors"


class GarageError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Point(ContractModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class LocationCandidate(Point):
    label: str
    precise: bool = False


class Garage(Point):
    osm_id: str
    name: str
    address: str | None = None
    distance_m: int
    directions_url: str
    osm_url: str


class GaragePreview(ContractModel):
    status: Literal["ready", "no_results", "needs_location"]
    origin: Point | None = None
    radius_m: int | None = None
    garages: list[Garage] = Field(default_factory=list)
    location_candidates: list[LocationCandidate] = Field(default_factory=list)
    sms_body: str | None = None
    attribution: str = ATTRIBUTION
    attribution_url: str = "https://www.openstreetmap.org/copyright"


def distance_m(a: Point, b: Point) -> float:
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(b.longitude - a.longitude)
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(min(1, max(0, value))))


def directions_url(point: Point) -> str:
    # Omitting origin lets Maps use the driver's current position.
    return "https://www.google.com/maps/dir/?" + urlencode({
        "api": "1", "destination": f"{point.latitude:.6f},{point.longitude:.6f}", "travelmode": "driving",
    })


def clean_text(value: object, limit: int = 90) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OsmGarageFinder:
    def __init__(self, *, open_url=None):
        self.open_url = open_url or build_opener(NoRedirects()).open
        self.overpass_url = os.getenv("OSM_OVERPASS_URL", "https://overpass-api.de/api/interpreter")
        self.nominatim_url = os.getenv("OSM_NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
        self.user_agent = os.getenv("OSM_USER_AGENT", "ClaimroomGarageLookup/1.0")
        self._lock = threading.Lock()
        self._last_geocode = 0.0
        self._cache: OrderedDict[str, tuple[float, object]] = OrderedDict()

    def _json(self, url: str, data: bytes | None = None, *, timeout: int = 8):
        request = Request(url, data=data, headers={
            "User-Agent": self.user_agent, "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        try:
            with self.open_url(request, timeout=timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("oversize response")
                return json.loads(raw)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            raise GarageError("osm_unavailable") from None

    def _cached(self, key: str, ttl: int, fetch):
        # One shared finder per process; bounded cache and serialized geocoding.
        with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < ttl:
                self._cache.move_to_end(key)
                return cached[1]
            value = fetch()
            self._cache[key] = (time.monotonic(), value)
            self._cache.move_to_end(key)
            while len(self._cache) > 256:
                self._cache.popitem(last=False)
            return value

    def _nominatim(self, parameters: dict):
        # Called under the shared cache lock, for both addresses and fallback POIs.
        delay = 1.1 - (time.monotonic() - self._last_geocode)
        if delay > 0:
            time.sleep(delay)
        self._last_geocode = time.monotonic()
        return self._json(self.nominatim_url + "?" + urlencode(parameters))

    def geocode(self, address: str) -> tuple[Point | None, list[LocationCandidate]]:
        rows = self._cached("geocode:" + address, 86400, lambda: self._nominatim({
            "q": address, "format": "jsonv2", "limit": 3, "addressdetails": 1,
        }))
        if not isinstance(rows, list):
            raise GarageError("osm_invalid_response")
        candidates = []
        precise = []
        for row in rows:
            try:
                candidate = LocationCandidate(latitude=row["lat"], longitude=row["lon"], label=row["display_name"])
                south, north, west, east = map(float, row["boundingbox"])
                extent = distance_m(Point(latitude=south, longitude=west), Point(latitude=north, longitude=east))
            except (KeyError, TypeError, ValueError, ValidationError):
                continue
            candidates.append(candidate)
            candidate.precise = extent <= 1000
            precise.append(extent <= 1000)
        # Never silently select a town centre or the first of ambiguous matches.
        origin = Point(**candidates[0].model_dump(include={"latitude", "longitude"})) if len(candidates) == 1 and precise[0] else None
        return origin, candidates

    def _garage_payload(self, query: str, origin: Point):
        try:
            payload = self._json(self.overpass_url, urlencode({"data": query}).encode(), timeout=5)
            if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list) or payload.get("remark"):
                raise GarageError("osm_invalid_response")
            return payload
        except GarageError:
            # Nominatim provides a bounded selection of POIs, not an exhaustive
            # inventory. Only use real car-repair matches within the search radius.
            latitude_delta = RADII[-1] / 111195
            longitude_delta = min(180, latitude_delta / max(.01, math.cos(math.radians(origin.latitude))))
            rows = self._nominatim({
                "q": "[car repair]", "format": "jsonv2", "limit": 40, "addressdetails": 1,
                "bounded": 1,
                "viewbox": f"{max(-180, origin.longitude - longitude_delta)},{min(90, origin.latitude + latitude_delta)},"
                           f"{min(180, origin.longitude + longitude_delta)},{max(-90, origin.latitude - latitude_delta)}",
            })
            if not isinstance(rows, list):
                raise GarageError("osm_invalid_response")
            elements = []
            for row in rows:
                if not isinstance(row, dict) or row.get("category", row.get("class")) != "shop" or row.get("type") != "car_repair":
                    continue
                try:
                    point = Point(latitude=row["lat"], longitude=row["lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                kind, osm_id = row.get("osm_type"), row.get("osm_id")
                if kind not in {"node", "way", "relation"} or not isinstance(osm_id, int) or distance_m(origin, point) > RADII[-1]:
                    continue
                address = row.get("address") if isinstance(row.get("address"), dict) else {}
                tags = {"shop": "car_repair", "name": clean_text(row.get("name")),
                        "addr:housenumber": address.get("house_number"), "addr:street": address.get("road"),
                        "addr:postcode": address.get("postcode"), "addr:city": address.get("city") or address.get("town") or address.get("village")}
                coordinates = {"lat": point.latitude, "lon": point.longitude}
                elements.append({"type": kind, "id": osm_id, "tags": tags,
                                 **(coordinates if kind == "node" else {"center": coordinates})})
            if not elements:
                # A failed primary lookup and empty fallback do not prove absence.
                raise GarageError("osm_unavailable")
            return {"elements": elements}

    def nearby(self, origin: Point) -> tuple[int, list[Garage]]:
        # One bounded Overpass request; apply expanding circles locally to avoid
        # four costly public API requests for an empty rural location.
        query = (
            '[out:json][timeout:4];nwr["shop"="car_repair"]'
            f'(around:{RADII[-1]},{origin.latitude},{origin.longitude});out center tags;'
        )
        payload = self._cached("garages:" + query, 900, lambda: self._garage_payload(query, origin))
        if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list) or payload.get("remark"):
            # Overpass can return HTTP 200 with a runtime error / partial results.
            raise GarageError("osm_invalid_response")
        found: list[tuple[float, Garage]] = []
        seen: set[str] = set()
        for item in payload["elements"]:
            if not isinstance(item, dict):
                continue
            tags = item.get("tags", {})
            if not isinstance(tags, dict) or tags.get("shop") != "car_repair":
                continue
            if tags.get("disused") == "yes" or tags.get("abandoned") == "yes":
                continue
            try:
                coordinates = item if item.get("type") == "node" else item["center"]
                point = Point(latitude=coordinates["lat"], longitude=coordinates["lon"])
                if item["type"] not in {"node", "way", "relation"} or not isinstance(item["id"], int):
                    continue
                osm_id = f'{item["type"]}/{item["id"]}'
            except (KeyError, TypeError, ValidationError):
                continue
            distance = distance_m(origin, point)
            if distance > RADII[-1] or osm_id in seen:
                continue
            seen.add(osm_id)
            name = clean_text(tags.get("name") or tags.get("brand") or tags.get("operator")) or "Garage automobile"
            # OSM may represent the same business as both a node and a building.
            if any(g.name.casefold() == name.casefold() and distance_m(g, point) < 30 for _, g in found):
                continue
            street = " ".join(filter(None, [clean_text(tags.get("addr:housenumber")), clean_text(tags.get("addr:street"))]))
            locality = " ".join(filter(None, [clean_text(tags.get("addr:postcode")), clean_text(tags.get("addr:city"))]))
            address = clean_text(tags.get("addr:full")) or ", ".join(filter(None, [street, locality])) or None
            found.append((distance, Garage(
                **point.model_dump(), osm_id=osm_id, name=name, address=address,
                distance_m=round(distance), directions_url=directions_url(point),
                osm_url="https://www.openstreetmap.org/" + osm_id,
            )))
        found.sort(key=lambda pair: (pair[0], pair[1].osm_id))
        for radius in RADII:
            in_radius = [garage for distance, garage in found if distance <= radius]
            if in_radius:
                return radius, in_radius[:3]
        return RADII[-1], []

    def preview(self, location: str | None, origin: Point | None = None) -> GaragePreview:
        candidates = []
        if origin is None and location and location.strip():
            origin, candidates = self.geocode(location.strip())
        if origin is None:
            return GaragePreview(status="needs_location", location_candidates=candidates)
        radius, garages = self.nearby(origin)
        if not garages:
            body = "Photos reçues. Aucun garage référencé dans OpenStreetMap à moins de 5 km du lieu du sinistre. Contactez votre conseiller pour poursuivre la recherche.\nDonnées : © OpenStreetMap contributors — https://www.openstreetmap.org/copyright"
        else:
            lines = ["Photos reçues. Garages près du lieu du sinistre (distances à vol d'oiseau) :"]
            for index, garage in enumerate(garages, 1):
                distance = f"{garage.distance_m} m" if garage.distance_m < 1000 else f"{garage.distance_m / 1000:.1f} km"
                lines += [f"{index}. {garage.name} — {distance}", garage.directions_url]
            lines += ["Appelez avant de vous déplacer. Libre choix du réparateur ; prise en charge à confirmer.", "Données : © OpenStreetMap contributors — https://www.openstreetmap.org/copyright"]
            body = "\n".join(lines)
        return GaragePreview(status="ready" if garages else "no_results", origin=origin, radius_m=radius, garages=garages, sms_body=body)
