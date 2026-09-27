"""Exact fixture lookups sourced from current Gemini plate observations."""

import re

from claim_api.mock_insurance import (
    PATTERNS,
    SOURCE_VERSION,
    insurance_lookup,
    correspondent_lookup,
    normalize_plate,
)


def lookup_media_plates(case):
    run = case.latest_analysis
    if not (
        run
        and run.status == "ready"
        and run.input_content_revision == case.content_revision
        and run.method_version.startswith(("gemini-joint-media-v1:", "gemini-joint-media-v2:"))
        and run.output
        and run.output.media_analysis
    ):
        return []
    results = []
    for finding in run.output.media_analysis.plates:
        plate = normalize_plate(finding.plate or "")
        countries = [
            country
            for country, pattern in PATTERNS.items()
            if re.fullmatch(pattern, plate)
        ]
        entry = {
            "vehicle": finding.vehicle,
            "role": finding.role,
            "plate": finding.plate,
            "citations": [c.model_dump(mode="json") for c in finding.citations],
            "analysis_run_id": str(run.id),
            "source_version": SOURCE_VERSION,
            "synthetic": True,
            "requires_human_review": True,
        }
        if finding.legibility != "readable" or len(countries) != 1:
            results.append(
                {
                    **entry,
                    "status": "ambiguous",
                    "reason": "plate_incomplete_or_invalid",
                    "data": {},
                }
            )
            continue
        if not case.intake.incident_at:
            results.append(
                {
                    **entry,
                    "status": "unavailable",
                    "reason": "incident_date_required",
                    "data": {},
                }
            )
            continue
        country = countries[0]
        result = insurance_lookup(plate, country, case.intake.incident_at.date())
        entry.update(
            country=country,
            status=result.status,
            reason=result.reason,
            data=result.data,
            query=result.query,
        )
        if result.status == "matched" and country == "UK":
            correspondent = correspondent_lookup(
                result.data["insurer_id"], "FR", case.intake.incident_at.date()
            )
            entry["correspondent"] = {
                "status": correspondent.status,
                "data": correspondent.data,
                "reason": correspondent.reason,
            }
        results.append(entry)
    return results
