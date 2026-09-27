"""Offline Pydantic Evals for the exact mock-insurance-v2 lookup probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from claim_api.mock_insurance import (
    FIXTURE_ROOT,
    SOURCE_VERSION,
    correspondent_lookup,
    insurance_lookup,
)


PROBES_PATH = FIXTURE_ROOT / "eval_cases.json"
DATASET_NAME = "claims-insurance-probes-v2"
EVALUATOR_VERSION = "claims-insurance-probes-evaluator-v1"


class LookupObservation(BaseModel):
    """Only synthetic lookup outcomes; no plate, policy or insurer is exported."""

    model_config = ConfigDict(extra="forbid")

    status: str
    reason: str | None
    valid_on_incident_date: bool | None
    insurer_name_hash: str | None
    correspondent_status: str | None
    correspondent_name_hash: str | None


def _name_hash(value: str | None) -> str | None:
    return hashlib.sha256(value.encode()).hexdigest() if value else None


def _load_probes() -> tuple[list[dict[str, Any]], str]:
    raw = PROBES_PATH.read_bytes()
    probes = json.loads(raw)
    if SOURCE_VERSION != "mock-insurance-v2" or not isinstance(probes, list) or len(probes) != 20:
        raise ValueError("Expected exactly 20 mock-insurance-v2 probes from PR 20.")
    ids = [probe["dataset_case_id"] for probe in probes]
    if len(set(ids)) != len(ids):
        raise ValueError("Insurance probe IDs must be unique.")
    return probes, hashlib.sha256(raw).hexdigest()


class LookupAssertions(Evaluator[str, LookupObservation, dict[str, Any]]):
    def evaluate(self, ctx: EvaluatorContext[str, LookupObservation, dict[str, Any]]) -> dict[str, bool]:
        expected, actual = ctx.expected_output, ctx.output
        assertions = {
            "expected_status": actual.status == expected["status"],
            "expected_reason": actual.reason == expected["reason"],
            "coverage_on_incident_date": actual.valid_on_incident_date == expected["valid_on_incident_date"],
        }
        if expected["insurer_name_expected"]:
            assertions["expected_insurer_identity"] = actual.insurer_name_hash == expected["insurer_name_hash"]
        if expected["correspondent_status"] is not None:
            assertions["expected_correspondent_status"] = actual.correspondent_status == expected["correspondent_status"]
        if expected["correspondent_name_expected"]:
            assertions["expected_correspondent_identity"] = actual.correspondent_name_hash == expected["correspondent_name_hash"]
        return assertions

    def get_evaluator_version(self) -> str:
        return EVALUATOR_VERSION


def build_dataset() -> tuple[Dataset[str, LookupObservation, dict[str, Any]], Any, str]:
    """The task sees opaque probe IDs; the reference answers stay in the evaluator."""
    probes, fixture_sha256 = _load_probes()
    # Do not put expected results in the task's input map. This matters when
    # replacing the deterministic adapter with an evaluated agent later.
    inputs_by_id = {
        probe["dataset_case_id"]: {
            "plate": probe["plate"],
            "country": probe["country"],
            "incident_date": probe["incident_date"],
        }
        for probe in probes
    }
    cases = [
        Case(
            name=probe["dataset_case_id"],
            inputs=probe["dataset_case_id"],
            expected_output={
                "status": probe["expected_status"],
                "reason": probe["expected_reason"],
                "valid_on_incident_date": probe["expected_valid_on_incident_date"],
                "insurer_name_expected": "expected_insurer_name" in probe,
                "insurer_name_hash": _name_hash(probe.get("expected_insurer_name")),
                "correspondent_status": probe.get("expected_correspondent_status"),
                "correspondent_name_expected": "expected_correspondent_name" in probe,
                "correspondent_name_hash": _name_hash(probe.get("expected_correspondent_name")),
            },
        )
        for probe in probes
    ]

    def task(probe_id: str) -> LookupObservation:
        probe_input = inputs_by_id[probe_id]
        incident_date = date.fromisoformat(probe_input["incident_date"])
        lookup = insurance_lookup(probe_input["plate"], probe_input["country"], incident_date)
        correspondent = None
        if lookup.status == "matched" and probe_input["country"] == "UK":
            correspondent = correspondent_lookup(lookup.data["insurer_id"], "FR", incident_date)
        return LookupObservation(
            status=lookup.status,
            reason=lookup.reason,
            valid_on_incident_date=lookup.data["valid_on_incident_date"],
            insurer_name_hash=_name_hash(lookup.data.get("insurer_name")),
            correspondent_status=correspondent.status if correspondent else None,
            correspondent_name_hash=_name_hash(correspondent.data.get("correspondent_name")) if correspondent else None,
        )

    return Dataset[str, LookupObservation, dict[str, Any]](
        name=DATASET_NAME,
        cases=cases,
        evaluators=[LookupAssertions()],
    ), task, fixture_sha256


def run(*, upload_to_logfire: bool = False) -> tuple[Any, dict[str, Any]]:
    dataset, task, fixture_sha256 = build_dataset()
    run_id = str(uuid4())
    metadata = {
        "dataset_id": "claims-insurance-probes",
        "dataset_version": 2,
        "fixture_version": SOURCE_VERSION,
        "fixture_sha256": fixture_sha256,
        "evaluator_version": EVALUATOR_VERSION,
        "run_id": run_id,
        "capability": "exact_mock_insurance_lookup",
    }
    if upload_to_logfire:
        token = os.environ.get("LOGFIRE_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Logfire upload requested, but LOGFIRE_TOKEN is unset or empty.")
        import logfire

        try:
            os.environ.setdefault("LOGFIRE_BASE_URL", "https://logfire-eu.pydantic.dev")
            logfire.configure(
                token=token,
                send_to_logfire=True,
                service_name="claims-insurance-evals",
                service_version="probes-v2",
                environment=os.environ.get("LOGFIRE_ENVIRONMENT", "eval"),
            )
        except Exception as exc:
            raise RuntimeError(f"Could not configure Logfire upload: {type(exc).__name__}: {exc}") from exc

    experiment_name = f"{DATASET_NAME}-{run_id[:8]}"
    report = dataset.evaluate_sync(task, name=experiment_name, metadata=metadata, progress=False)
    results = []
    for case in report.cases:
        assertions = {name: bool(item.value) for name, item in case.assertions.items()}
        results.append({"probe_id": case.name, "passed": bool(assertions) and all(assertions.values()), "scores": assertions})
    summary = {
        **metadata,
        "dataset_name": DATASET_NAME,
        "experiment_name": experiment_name,
        "total_cases": len(results),
        "passed_cases": sum(result["passed"] for result in results),
        "failed_cases": sum(not result["passed"] for result in results),
        "case_results": results,
        "assertion_pass_rate": report.averages().assertions if report.averages() else None,
        "uploaded_to_logfire": upload_to_logfire,
    }
    if upload_to_logfire:
        import logfire

        if not logfire.force_flush(timeout_millis=10_000):
            raise RuntimeError("Logfire did not confirm an upload flush within 10 seconds.")
    return report, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline mock-insurance-v2 Pydantic Evals probes.")
    parser.add_argument("--upload-to-logfire", action="store_true")
    args = parser.parse_args(argv)
    try:
        _, summary = run(upload_to_logfire=args.upload_to_logfire)
    except RuntimeError as exc:
        print(f"evals: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
