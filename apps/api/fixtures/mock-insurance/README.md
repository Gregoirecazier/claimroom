# Synthetic FR/UK insurance lookup database

**2,000 invented records: exactly 1,000 French-style plates and 1,000 UK-style plates.** No record was scraped, checked against a registration service, or linked to a real driver. Plate strings imitate visual formats only; they are not assertions of valid or unallocated registrations. The insurer/correspondent associations are entirely fictional. Never route these fixtures to real insurer services.

## Files

- `fr.json`, `uk.json`: versioned runtime lookup records.
- `fr.csv`, `uk.csv`: the same rows for manual inspection/import; blank CSV values correspond to JSON `null`.
- `eval_cases.json`: 20 labelled evaluation probes, separate from runtime input: 12 original cases, the six G1/G2/G3 vehicle references, and two G2 uncertainty controls.
- `../../scripts/generate_mock_insurance.py`: deterministic generator and optional SQLite exporter.
- `../../claim_api/mock_insurance.py`: exact offline lookup and demo correspondent directory.

Each record has a stable fixture ID, synthetic flag, source version, country, display/normalized plate, vehicle description, fictional insurer/policy, inclusive coverage dates and optional French correspondent. `vehicle_model` is null when the exact model is not established, as for the G1 Peugeot sedan. No owner names, addresses or personal phone numbers are generated.

Version `mock-insurance-v2` replaces five generic active-policy rows with the missing G1/G2/G3 plates. Counts remain exactly 1,000 per country. The random sequence, other vehicle rows, legacy reference plates, policy dates, insurers and correspondent distribution are preserved; every exported row carries the new source version. Regeneration keeps these scenario references in JSON, CSV and optional SQLite exports.

## Distribution at the reference date: 25 September 2026

| Coverage outcome | FR | UK |
| --- | ---: | ---: |
| Active on that date | 800 | 800 |
| Expired on 24 September | 100 | 100 |
| Starts on 26 September | 50 | 50 |
| Vehicle row exists, policy unknown | 50 | 50 |
| **Total** | **1,000** | **1,000** |

Of the 800 active UK rows, 50 use an insurer with no French correspondent in this mock directory. Absent-plate probes are outside the 2,000 rows. No policy found means missing mock data, not proof that a real vehicle is uninsured.

Dates are evaluated against the supplied accident date, not today's date. Active rows cover 1 January–31 December 2026; expired and future rows use the bounds above. The correspondent directory models calendar year 2026 only. It is a lookup fixture, not a legal route determination.

## Plates ready for the demo

| Plate | Country | Expected outcome on 2026-09-25 |
| --- | --- | --- |
| AA-123-AA | FR | Silver Peugeot 208, active policy. |
| FR-482-KL | FR | G1 silver Peugeot sedan, exact model unspecified; active policy, Azur Demo Auto. |
| AB12 CDE | UK | Black BMW 3 Series, Northbridge Demo Motor → Hexagone Demo Recours. |
| GH-271-RM | FR | G2 silver Renault Megane, active policy, Loire Demo Motor. |
| RK18 LXP | UK | G2 scenario reference: black Opel Astra, active policy, Westhaven Demo Motor → Loire Demo Recours. The actual video reading remains uncertain between LXP and LYP. |
| GT-638-VN | FR | G3 light grey Citroen C3, active policy, Alpes Demo Assurance. |
| LM21 RZT | UK | G3 dark red Toyota Corolla, active policy, Birchwood Demo Cover → Alpes Demo Recours. |
| XY34 ZTR | UK | Legacy blue Ford Focus reference, active policy, **uninvolved** in the original parasite scenario. The supplied G2 file shows XY34 ZTR on the blue uninvolved car at sampled frames 3.000 s and 4.792 s, superseding the initial human annotation. Associate that visual reading with the blue car only; the registry model remains a lookup fact, not a visual fact. |
| RK18 LYP | UK | Alternative uncertain G2 reading; absent from this dataset, not a second identity for the Opel. Its absence does not prove that the visible plate is LXP. |
| AB13 CDE / AA-124-AA | UK / FR | Expired coverage. |
| AB14 CDE / AA-125-AA | UK / FR | Coverage not yet started. |
| AB15 CDE / AA-126-AA | UK / FR | Existing vehicle, no policy data. |
| AB16 CDE | UK | Active policy but no correspondent. |
| ZZ99 ZZZ / ZZ-999-ZZ | UK / FR | Plate absent from the database. |
| AB12 CD? | UK | Partial plate: ambiguous; never fill in the missing character. |
| RK18 L?P | UK | Partial G2 plate: ambiguous; never select LXP from the database to fill in the missing character. |

The earlier demo's `DEMO-FR-POL-001` is a narrative label; the actual generated fixture for AA-123-AA uses `DEMO-FR-POL-0001`. Use values from the exported record when seeding a dossier. The original Lille `complete`/`ambiguous` fixtures are left unchanged.

## Use locally

From `apps/api`, in the project's Python environment:

```sh
python -m claim_api.mock_insurance 'AB12 CDE' --country UK --date 2026-09-25
python -m claim_api.mock_insurance 'RK18 LXP' --country UK --date 2026-09-25
python -m claim_api.mock_insurance 'LM21 RZT' --country UK --date 2026-09-25
python -m claim_api.mock_insurance 'AB13 CDE' --country UK --date 2026-09-25
python scripts/generate_mock_insurance.py
python scripts/generate_mock_insurance.py --sqlite /tmp/new-mock-insurance.sqlite3
```

The SQLite path must not already exist. Its `mock_policies` table contains the same 2,000 rows; `(country, plate_normalized)` is unique. Example query:

```sql
SELECT plate, insurer_name, coverage_start, coverage_end
FROM mock_policies
WHERE country = 'UK' AND plate_normalized = 'AB12CDE';
```

Coverage is not established by this raw SQL query alone; use the date-aware lookup or check both coverage bounds.

## Adapter behavior and integration with Ben's schema

`insurance_lookup(plate, country, incident_date)` and `correspondent_lookup(insurer_id, accident_country, incident_date)` return the existing `ProviderFixture` shape. The CLI wraps results with `mode=mock` and `source_version=mock-insurance-v2`.

- Complete exact plate + valid coverage → `matched`.
- Absent plate, absent policy or inactive coverage → `no_match`, with different reason codes. An expired policy may retain its historical insurer with `valid_on_incident_date=false`; it must not be treated as an active match.
- Partial/malformed plate → `ambiguous`, no candidate guessing.
- Unsupported country → `unavailable`. `GB` input is normalized to dataset country `UK`.
- Spaces, hyphens and case are normalized; characters such as O/0 are never substituted.
- A vehicle description from the mock registry is a lookup fact, never a visual observation.

The G2 video reference accepts uncertain or partial OCR. The full-string LXP and LYP lookup probes test registry behavior only. An LXP match and LYP miss must not be treated as visual disambiguation, and the agent must not receive the held-out annotation as input. A matched Toyota policy in G3 likewise does not imply that the Toyota caused the collision.

This PR supplies fixtures, callable adapters, generator and tests. It does **not** add an API endpoint, change the shared Supabase schema, or automatically connect analysis to this dataset. Ben should call the adapter after plate corroboration, persist the result in `provider_results` with its source version and query identity, then pass the persisted source UUID into analysis. Fixture `record_id` strings are not database source UUIDs. A fixture-only plate lookup cannot decide which vehicle caused a collision.

Do not include `eval_cases.json` or annotations in the model's input. Hold out cases/media when measuring generalization: 2,000 rows increase lookup variety, not the number of independent video scenes. For alternate outcomes on the exact same AB12 CDE video, use an isolated provider override with a distinct fixture version; do not mutate the shared happy-path row or create duplicate plates.

If generated footage renders a different readable plate, annotate the actual media and deliberately update the fixture/generator, bump its source version and regenerate. Never silently map every unknown plate to the demo insurer.

## Validation

Run `python -m pytest tests/test_mock_insurance.py` from `apps/api`. Tests cover counts/uniqueness, reproducibility, JSON/CSV parity, the 20 eval probes, G1/G2/G3 vehicle identities, insurers/correspondents, date boundaries, exact normalization, missing correspondents, SQLite export and duplicate prevention.
