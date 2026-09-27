"""Exact, offline policy lookups over explicitly synthetic FR/UK data."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from datetime import date
from functools import lru_cache
from pathlib import Path

from claim_api.fixtures import ProviderFixture

SOURCE_VERSION = "mock-insurance-v2"
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "mock-insurance"
PATTERNS = {"FR": r"[A-Z]{2}[0-9]{3}[A-Z]{2}", "UK": r"[A-Z]{2}[0-9]{2}[A-Z]{3}"}


def normalize_plate(plate: str) -> str:
    # Formatting normalization only. Never repair O/0, guess a character, or fuzzy-match.
    return re.sub(r"[\s-]", "", plate.upper())


@lru_cache(maxsize=1)
def _records() -> dict[tuple[str, str], dict]:
    index = {}
    for country in ("FR", "UK"):
        rows = json.loads((FIXTURE_ROOT / f"{country.lower()}.json").read_text())
        for row in rows:
            key = (row["country"], row["plate_normalized"])
            if row["synthetic"] is not True or row["source_version"] != SOURCE_VERSION or key in index:
                raise ValueError("Invalid synthetic fixture version, provenance, or duplicate plate")
            index[key] = row
    return index


def insurance_lookup(plate: str, country: str, incident_date: date, *, record_lookup=None) -> ProviderFixture:
    country = country.strip().upper()
    if country == "GB":
        country = "UK"
    plate = normalize_plate(plate)
    query = {"plate_candidate": plate, "country": country, "incident_date": incident_date.isoformat()}
    data = {"synthetic": True, "coverage": "not_established", "valid_on_incident_date": None}

    def result(status, reason=None):
        return ProviderFixture("insurance_lookup", status, query, data.copy(), reason)

    if country not in PATTERNS:
        return result("unavailable", "unsupported_country")
    if not re.fullmatch(PATTERNS[country], plate):
        return result("ambiguous", "plate_incomplete_or_invalid")
    record = record_lookup(country, plate) if record_lookup is not None else _records().get((country, plate))
    if record is None:
        return result("no_match", "plate_not_found")
    data.update(record_id=record["record_id"], country=country, plate=record["plate"])
    if record["policy_reference"] is None:
        return result("no_match", "policy_not_found")
    data.update({name: record[name] for name in (
        "insurer_id", "insurer_name", "policy_reference", "coverage_start", "coverage_end", "driver_name", "driver_email", "insurer_contact_name", "insurer_email"
    )})
    start, end = date.fromisoformat(record["coverage_start"]), date.fromisoformat(record["coverage_end"])
    active = start <= incident_date <= end
    data["valid_on_incident_date"] = active
    data["coverage"] = "matched" if active else "inactive"
    if not active:
        return result("no_match", "coverage_not_started" if incident_date < start else "coverage_expired")
    return result("matched")


def correspondent_lookup(insurer_id: str, accident_country: str, incident_date: date) -> ProviderFixture:
    """Demo directory for a UK insurer's French correspondent, not a legal routing rule."""
    accident_country = accident_country.strip().upper()
    query = {"insurer_id": insurer_id, "accident_country": accident_country, "incident_date": incident_date.isoformat()}
    data = {"synthetic": True}
    if accident_country != "FR":
        return ProviderFixture("correspondent_lookup", "unavailable", query, data, "unsupported_accident_country")
    # This version deliberately models one directory valid during 2026 only.
    if not date(2026, 1, 1) <= incident_date <= date(2026, 12, 31):
        return ProviderFixture("correspondent_lookup", "unavailable", query, data, "directory_date_out_of_range")
    matches = [r for r in _records().values() if r["country"] == "UK" and r["insurer_id"] == insurer_id and r["correspondent_fr_id"]]
    if not matches:
        return ProviderFixture("correspondent_lookup", "no_match", query, data, "correspondent_not_found")
    record = matches[0]
    data.update(correspondent_id=record["correspondent_fr_id"], correspondent_name=record["correspondent_fr_name"],
                country="FR", directory_reference=f"{SOURCE_VERSION}/{record['correspondent_fr_id']}")
    return ProviderFixture("correspondent_lookup", "matched", query, data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plate")
    parser.add_argument("--country", required=True)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    lookup = insurance_lookup(args.plate, args.country, args.date)
    results = [lookup]
    if lookup.status == "matched" and lookup.data["country"] == "UK":
        results.append(correspondent_lookup(lookup.data["insurer_id"], "FR", args.date))
    print(json.dumps({"mode": "mock", "source_version": SOURCE_VERSION,
                      "results": [asdict(r) for r in results]}, indent=2))
