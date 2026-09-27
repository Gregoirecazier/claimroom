"""Reproducible synthetic FR/UK policy fixtures; never queries real registries."""
from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
from pathlib import Path

VERSION = "mock-insurance-v2"
ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "mock-insurance"
LETTERS = "ABCDEFGHJKLMNPRSTVWXYZ"
RESERVED = {
    "FR": {0: "AA-123-AA", 800: "AA-124-AA", 900: "AA-125-AA", 950: "AA-126-AA"},
    "UK": {0: "AB12 CDE", 1: "XY34 ZTR", 750: "AB16 CDE", 800: "AB13 CDE", 900: "AB14 CDE", 950: "AB15 CDE"},
}
# Replace only these generic active-policy slots after generation, preserving
# every other seeded row and the existing policy/correspondent distributions.
# The Peugeot's exact model is not established; do not invent a model badge.
SCENARIO_VEHICLES = {
    "FR": {
        1: ("FR-482-KL", "Peugeot", None, "silver"),
        2: ("GH-271-RM", "Renault", "Megane", "silver"),
        3: ("GT-638-VN", "Citroen", "C3", "light grey"),
    },
    "UK": {
        2: ("RK18 LXP", "Opel", "Astra", "black"),
        3: ("LM21 RZT", "Toyota", "Corolla", "dark red"),
    },
}
ABSENT = {"FR": "ZZ-999-ZZ", "UK": "ZZ99 ZZZ"}
NAMES = {
    "FR": ["Hexagone Demo Assurance", "Azur Demo Auto", "Loire Demo Motor", "Alpes Demo Assurance", "Rivage Demo Auto"],
    "UK": ["Northbridge Demo Motor", "Oakfield Demo Insurance", "Westhaven Demo Motor", "Birchwood Demo Cover", "Meadowbrook Demo Auto"],
}


def generate_rows(country: str) -> list[dict]:
    rng = random.Random(260925 if country == "FR" else 260926)
    used = set(RESERVED[country].values()) | {ABSENT[country]}
    rows = []
    for i in range(1000):
        plate = RESERVED[country].get(i)
        while plate is None:
            letters = lambda n: "".join(rng.choice(LETTERS) for _ in range(n))
            candidate = (f"{letters(2)}-{rng.randint(1, 999):03d}-{letters(2)}" if country == "FR"
                         else f"{letters(2)}{rng.choice(['12','15','18','20','21','22','23','24','25','62','65','68','70','71','72','73','74','75'])} {letters(3)}")
            if candidate not in used:
                plate = candidate
                used.add(plate)
        known = i < 950
        no_correspondent = country == "UK" and 750 <= i < 800
        insurer_id = f"DEMO-{country}-INS-{(5 if no_correspondent else i % 5):02d}"
        insurer_name = (("Harbour Demo Unlisted Motor" if no_correspondent else NAMES[country][i % 5]) + " (fictional)")
        starts = "2026-09-26" if 900 <= i < 950 else "2026-01-01"
        ends = "2026-09-24" if 800 <= i < 900 else "2026-12-31"
        make, model = rng.choice([("Peugeot", "208"), ("Renault", "Clio"), ("Toyota", "Yaris"), ("BMW", "3 Series"), ("Ford", "Focus"), ("Volkswagen", "Golf")])
        color = rng.choice(["silver", "black", "white", "blue", "red", "grey"])
        if i == 0:
            make, model, color = ("Peugeot", "208", "silver") if country == "FR" else ("BMW", "3 Series", "black")
        if country == "UK" and i == 1:
            make, model, color = "Ford", "Focus", "blue"
        rows.append({
            "record_id": f"DEMO-{country}-{i + 1:04d}", "synthetic": True,
            "source_version": VERSION, "country": country, "plate": plate,
            "plate_normalized": plate.replace("-", "").replace(" ", ""),
            "vehicle_make": make, "vehicle_model": model, "vehicle_color": color,
            "insurer_id": insurer_id, "insurer_name": insurer_name,
            "driver_name": f"Conducteur fictif {country} {i+1:04d}",
            "driver_email": f"conducteur-{country.lower()}-{i+1:04d}@example.test",
            "insurer_contact_name": f"Gestionnaire fictif {insurer_id}",
            "insurer_email": f"sinistres@{insurer_id.lower()}.test",
            "policy_reference": f"DEMO-{country}-POL-{i + 1:04d}" if known else None,
            "coverage_start": starts if known else None, "coverage_end": ends if known else None,
            "correspondent_fr_id": f"DEMO-CORR-FR-{i % 5:02d}" if country == "UK" and known and not no_correspondent else None,
            "correspondent_fr_name": (["Hexagone Demo Recours", "Azur Demo Recours", "Loire Demo Recours", "Alpes Demo Recours", "Rivage Demo Recours"][i % 5] + " (fictional)") if country == "UK" and known and not no_correspondent else None,
        })
    for index, (plate, make, model, color) in SCENARIO_VEHICLES[country].items():
        rows[index].update(
            plate=plate, plate_normalized=plate.replace("-", "").replace(" ", ""),
            vehicle_make=make, vehicle_model=model, vehicle_color=color,
        )
    if len({row["plate_normalized"] for row in rows}) != len(rows):
        raise ValueError(f"Duplicate plate in {country} scenario fixtures")
    return rows


def eval_cases() -> list[dict]:
    cases = []
    for country in ("FR", "UK"):
        for name, plate, status, valid, reason in [
            ("active", RESERVED[country][0], "matched", True, None),
            ("expired", RESERVED[country][800], "no_match", False, "coverage_expired"),
            ("not_yet_active", RESERVED[country][900], "no_match", False, "coverage_not_started"),
            ("policy_unknown", RESERVED[country][950], "no_match", None, "policy_not_found"),
            ("plate_absent", ABSENT[country], "no_match", None, "plate_not_found"),
        ]:
            cases.append({"dataset_case_id": f"{country.lower()}_{name}", "country": country,
                          "plate": plate, "incident_date": "2026-09-25", "expected_status": status,
                          "expected_valid_on_incident_date": valid, "expected_reason": reason})
    cases += [
        {"dataset_case_id": "uk_partial", "country": "UK", "plate": "AB12 CD?", "incident_date": "2026-09-25", "expected_status": "ambiguous", "expected_valid_on_incident_date": None, "expected_reason": "plate_incomplete_or_invalid"},
        {"dataset_case_id": "uk_no_correspondent", "country": "UK", "plate": "AB16 CDE", "incident_date": "2026-09-25", "expected_status": "matched", "expected_valid_on_incident_date": True, "expected_reason": None, "expected_correspondent_status": "no_match"},
    ]
    for scenario, country, plate, insurer, correspondent in [
        ("g1", "FR", "FR-482-KL", "Azur Demo Auto (fictional)", None),
        ("g1", "UK", "AB12 CDE", "Northbridge Demo Motor (fictional)", "Hexagone Demo Recours (fictional)"),
        ("g2", "FR", "GH-271-RM", "Loire Demo Motor (fictional)", None),
        ("g2", "UK", "RK18 LXP", "Westhaven Demo Motor (fictional)", "Loire Demo Recours (fictional)"),
        ("g3", "FR", "GT-638-VN", "Alpes Demo Assurance (fictional)", None),
        ("g3", "UK", "LM21 RZT", "Birchwood Demo Cover (fictional)", "Alpes Demo Recours (fictional)"),
    ]:
        case = {"dataset_case_id": f"{scenario}_{country.lower()}_active", "country": country,
                "plate": plate, "incident_date": "2026-09-25", "expected_status": "matched",
                "expected_valid_on_incident_date": True, "expected_reason": None,
                "expected_insurer_name": insurer}
        if correspondent:
            case.update(expected_correspondent_status="matched", expected_correspondent_name=correspondent)
        cases.append(case)
    # Registry probes are separate from Vision: one existing candidate must not
    # turn an uncertain OCR result into a corroborated plate.
    cases += [
        {"dataset_case_id": "g2_uk_partial", "country": "UK", "plate": "RK18 L?P", "incident_date": "2026-09-25", "expected_status": "ambiguous", "expected_valid_on_incident_date": None, "expected_reason": "plate_incomplete_or_invalid"},
        {"dataset_case_id": "g2_uk_alternative_absent", "country": "UK", "plate": "RK18 LYP", "incident_date": "2026-09-25", "expected_status": "no_match", "expected_valid_on_incident_date": None, "expected_reason": "plate_not_found"},
    ]
    return cases


def build(output: Path, sqlite_path: Path | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for country in ("FR", "UK"):
        rows = generate_rows(country)
        all_rows.extend(rows)
        (output / f"{country.lower()}.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        with (output / f"{country.lower()}.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    (output / "eval_cases.json").write_text(json.dumps(eval_cases(), indent=2) + "\n", encoding="utf-8")
    if sqlite_path:
        # Refuse to overwrite a user's existing database.
        with sqlite_path.open("xb"):
            pass
        columns = list(all_rows[0])
        with sqlite3.connect(sqlite_path) as db:
            definitions = ", ".join(f"{c} {'INTEGER' if c == 'synthetic' else 'TEXT'}" for c in columns)
            db.execute(f"CREATE TABLE mock_policies ({definitions}, PRIMARY KEY(record_id), UNIQUE(country, plate_normalized), CHECK(synthetic = 1))")
            db.executemany(f"INSERT INTO mock_policies VALUES ({','.join('?' for _ in columns)})",
                           [tuple(row[c] for c in columns) for row in all_rows])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT)
    parser.add_argument("--sqlite", type=Path, help="Optional new SQLite file (must not already exist)")
    args = parser.parse_args()
    build(args.output, args.sqlite)
    print(f"Generated 1,000 FR + 1,000 UK synthetic records and {len(eval_cases())} eval cases.")
