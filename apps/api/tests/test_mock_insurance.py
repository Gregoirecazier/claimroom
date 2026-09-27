from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter
from datetime import date

import pytest

from claim_api.mock_insurance import FIXTURE_ROOT, SOURCE_VERSION, correspondent_lookup, insurance_lookup
from scripts.generate_mock_insurance import build, generate_rows

DAY = date(2026, 9, 25)


def test_dataset_counts_provenance_and_csv_match():
    for country, pattern in [("FR", r"[A-Z]{2}-[0-9]{3}-[A-Z]{2}"), ("UK", r"[A-Z]{2}[0-9]{2} [A-Z]{3}")]:
        rows = json.loads((FIXTURE_ROOT / f"{country.lower()}.json").read_text())
        assert len(rows) == len({r["plate_normalized"] for r in rows}) == 1000
        assert len({r["record_id"] for r in rows}) == 1000
        assert rows == generate_rows(country)
        assert all(r["synthetic"] is True and r["source_version"] == SOURCE_VERSION for r in rows)
        assert all(re.fullmatch(pattern, r["plate"]) for r in rows)
        outcomes = Counter(insurance_lookup(r["plate"], country, DAY).reason or "active" for r in rows)
        assert outcomes == {"active": 800, "coverage_expired": 100, "coverage_not_started": 50, "policy_not_found": 50}
        with (FIXTURE_ROOT / f"{country.lower()}.csv").open(newline="") as f:
            csv_rows = list(csv.DictReader(f))
        assert csv_rows == [{k: "" if v is None else str(v) for k, v in r.items()} for r in rows]


@pytest.mark.parametrize("case", json.loads((FIXTURE_ROOT / "eval_cases.json").read_text()), ids=lambda c: c["dataset_case_id"])
def test_evaluation_cases(case):
    result = insurance_lookup(case["plate"], case["country"], date.fromisoformat(case["incident_date"]))
    assert result.status == case["expected_status"]
    assert result.data["valid_on_incident_date"] == case["expected_valid_on_incident_date"]
    assert result.reason == case["expected_reason"]
    if "expected_insurer_name" in case:
        assert result.data["insurer_name"] == case["expected_insurer_name"]
    if "expected_correspondent_status" in case:
        correspondent = correspondent_lookup(result.data["insurer_id"], "FR", DAY)
        assert correspondent.status == case["expected_correspondent_status"]
        if "expected_correspondent_name" in case:
            assert correspondent.data["correspondent_name"] == case["expected_correspondent_name"]


@pytest.mark.parametrize("country,plate,make,model,color", [
    ("FR", "FR-482-KL", "Peugeot", None, "silver"),
    ("UK", "AB12 CDE", "BMW", "3 Series", "black"),
    ("FR", "GH-271-RM", "Renault", "Megane", "silver"),
    ("UK", "RK18 LXP", "Opel", "Astra", "black"),
    ("FR", "GT-638-VN", "Citroen", "C3", "light grey"),
    ("UK", "LM21 RZT", "Toyota", "Corolla", "dark red"),
])
def test_video_scenario_vehicle_identity_and_normalized_lookup(country, plate, make, model, color):
    rows = json.loads((FIXTURE_ROOT / f"{country.lower()}.json").read_text())
    matches = [row for row in rows if row["plate"] == plate]
    assert len(matches) == 1
    record = matches[0]
    assert (record["vehicle_make"], record["vehicle_model"], record["vehicle_color"]) == (make, model, color)
    normalized = plate.replace("-", "").replace(" ", "").lower()
    result = insurance_lookup(normalized, "GB" if country == "UK" else country, DAY)
    assert result.status == "matched"
    assert result.data["record_id"] == record["record_id"]
    assert result.data["valid_on_incident_date"] is True


def test_golden_plate_normalization_and_correspondent():
    result = insurance_lookup(" ab12-cde ", "gb", DAY)
    assert result.status == "matched"
    assert result.data["insurer_name"] == "Northbridge Demo Motor (fictional)"
    correspondent = correspondent_lookup(result.data["insurer_id"], "FR", DAY)
    assert correspondent.status == "matched"
    assert correspondent.data["correspondent_name"] == "Hexagone Demo Recours (fictional)"
    assert insurance_lookup("aa 123 aa", "FR", DAY).status == "matched"


def test_no_fuzzy_or_cross_country_match():
    assert insurance_lookup("AB12 CD?", "UK", DAY).status == "ambiguous"
    assert insurance_lookup("AB12 CDE", "FR", DAY).status != "matched"
    assert insurance_lookup("AB12 CDE", "BE", DAY).status == "unavailable"
    missing = insurance_lookup("ZZ99 ZZZ", "UK", DAY)
    assert missing.status == "no_match" and "insurer_name" not in missing.data


@pytest.mark.parametrize("day, expected", [(date(2025, 12, 31), False), (date(2026, 1, 1), True),
                                         (date(2026, 12, 31), True), (date(2027, 1, 1), False)])
def test_coverage_date_boundaries(day, expected):
    result = insurance_lookup("AB12 CDE", "UK", day)
    assert result.data["valid_on_incident_date"] is expected
    assert (result.status == "matched") is expected


def test_directory_scope_and_missing():
    assert correspondent_lookup("DEMO-UK-INS-00", "BE", DAY).status == "unavailable"
    assert correspondent_lookup("DEMO-UK-INS-00", "FR", date(2025, 1, 1)).status == "unavailable"
    assert correspondent_lookup("unknown", "FR", DAY).status == "no_match"
    assert correspondent_lookup("DEMO-FR-INS-00", "FR", DAY).status == "no_match"


def test_generated_sqlite_and_reproducible_exports(tmp_path):
    output, db_path = tmp_path / "exports", tmp_path / "policies.sqlite3"
    build(output, db_path)
    for committed in FIXTURE_ROOT.glob("*.json"):
        assert (output / committed.name).read_bytes() == committed.read_bytes()
    with sqlite3.connect(db_path) as db:
        assert db.execute("select country, count(*) from mock_policies group by country").fetchall() == [("FR", 1000), ("UK", 1000)]
        assert db.execute("select insurer_name from mock_policies where country='UK' and plate_normalized='AB12CDE'").fetchone()[0] == "Northbridge Demo Motor (fictional)"
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into mock_policies select * from mock_policies limit 1")
    with pytest.raises(FileExistsError):
        build(output, db_path)
