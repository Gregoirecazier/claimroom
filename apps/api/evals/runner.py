from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from claim_api.analysis import (
    AnalysisError,
    ClaimsAnalyzer,
    FixtureClaimsAnalyzer,
    build_analysis_input,
    evaluate_gates,
    validate_output,
)
from claim_api.fixtures import get_scenario
from claim_api.mock_insurance import SOURCE_VERSION as MOCK_INSURANCE_SOURCE_VERSION
from claim_api.models import (
    AnalysisInputV2, AnalysisOutputV1, AnalysisSourceRef, CaseView, CounterpartyLookupView,
    EvidenceView, ProviderResultView, VideoAnalysisLinkView,
)
from claim_api.video_reuse import configured_pipeline


ROOT = Path(__file__).parent
GOLDEN_PATH = ROOT / "golden_cases_v2.json"
NAMESPACE = UUID("72ae05a6-7a0a-4ecf-9a95-4e51ad5fc697")
EVALUATOR_VERSION = "claims-analysis-evaluators-v2"


class EvalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_valid: bool
    sources_valid: bool
    uncertainty_valid: bool
    uncertainty_present: bool
    proposed_route: str | None
    gate_statuses: list[str]
    recipient_present: bool
    liability_claim: bool
    can_draft: bool


def _load_golden() -> dict[str, Any]:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    if data.get("dataset_id") != "claims-analysis-golden" or data.get("version") != 2:
        raise ValueError("Unexpected golden dataset identity or version.")
    if len(data.get("cases", [])) != 5:
        raise ValueError("The version 2 golden dataset must contain exactly five cases.")
    return data


def _case_input(spec: dict[str, Any]) -> AnalysisInputV2:
    scenario = get_scenario(spec["scenario_id"])
    case_id = uuid5(NAMESPACE, f"golden-v2:{spec['id']}")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    providers = []
    for index, item in enumerate(scenario.provider_results):
        result_id = uuid5(NAMESPACE, f"golden-v2:{spec['id']}:provider:{index}")
        query = dict(item.query)
        data = dict(item.data)
        if spec.get("contradictory_coverage_date") and item.provider == "insurance_lookup":
            data["valid_on_incident_date"] = False
        providers.append(ProviderResultView(
            id=result_id,
            source_id=result_id,
            provider=item.provider,
            mode="mock",
            status=item.status,
            source_version=MOCK_INSURANCE_SOURCE_VERSION if spec["scenario_id"] == "complete" else "lookup-fixtures-v1",
            query_hash=hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest(),
            query=query,
            retrieved_at=now,
            data=data,
            reason=item.reason,
        ))
    intake = scenario.intake
    if spec.get("unsupported_liability_narrative"):
        intake = intake.model_copy(update={
            "narrative": (
                "French insured reports their car was struck in a hit-and-run by a UK-registered vehicle. "
                "The caller says the other driver was responsible; no independent liability finding has been made. "
                "The caller clearly read the unique plate UK-SYN-482."
            )
        })
    evidence = [] if spec.get("missing_cctv") else [EvidenceView(
        id=uuid5(NAMESPACE, f"golden-v2:{spec['id']}:cctv"),
        case_id=case_id,
        kind="cctv_frame",
        storage_path=f"{case_id}/synthetic-cctv-frame.png",
        source_kind="synthetic_cctv",
        mode="mock",
        mime_type="image/png",
        byte_size=128,
        client_sha256=None,
        checksum_status="verified",
        received_at=now,
    )]
    video_analyses = []
    lookup = None
    if spec["scenario_id"] == "complete":
        media_id = uuid5(NAMESPACE, f"golden-v2:{spec['id']}:scene-video")
        refs = [
            AnalysisSourceRef(kind="evidence", id=str(media_id), locator="video:0-1000"),
            AnalysisSourceRef(kind="evidence", id=str(media_id), locator="video:1000-2000"),
        ]
        evidence.append(EvidenceView(
            id=media_id, case_id=case_id, kind="scene_video", storage_path=f"{case_id}/synthetic-scene.mp4",
            source_kind="synthetic_fixture", mode="mock", mime_type="video/mp4", byte_size=128,
            client_sha256=None, checksum_status="verified", received_at=now,
        ))
        video_analyses = [VideoAnalysisLinkView(
            evidence_id=media_id, artifact_id=uuid5(NAMESPACE, f"golden-v2:{spec['id']}:artifact"),
            pipeline_fingerprint=configured_pipeline().fingerprint,
            observations=[
                {"start_ms": 0, "end_ms": 1000, "category": "visible_text", "status": "observed",
                 "vehicle_track_id": "track-1", "plate_candidate": "UK-SYN-482", "description": "Plate on involved vehicle"},
                {"start_ms": 1000, "end_ms": 2000, "category": "movement", "status": "observed",
                 "vehicle_track_id": "track-1", "description": "Vehicle movement at the collision"},
            ],
        )]
        insurance, correspondent = providers
        insurance.query.update(vehicle_track_id="track-1", identification_status="observed",
                               supporting_source_refs=[ref.model_dump() for ref in refs])
        insurance.data["insurer_id"] = "demo-uk-insurer"
        correspondent.query.clear()
        correspondent.query.update(insurer_id="demo-uk-insurer", accident_country="FR",
                                   incident_date="2025-06-14", insurance_result_id=str(insurance.id))
        for result in (insurance, correspondent):
            result.query_hash = hashlib.sha256(json.dumps(result.query, sort_keys=True).encode()).hexdigest()
        lookup = CounterpartyLookupView(
            plate_candidate="UK-SYN-482", country="UK", incident_date=intake.incident_at.date(),
            vehicle_track_id="track-1", identification_status="observed", supporting_source_refs=refs,
            insurance_result_id=insurance.id, correspondent_result_id=correspondent.id, current=True,
        )
    case = CaseView(
        id=case_id,
        created_by_user_id=uuid5(NAMESPACE, "synthetic-handler"),
        scenario_id=spec["scenario_id"],
        synthetic=True,
        status="collecting",
        state_version=1,
        content_revision=1,
        created_at=now,
        updated_at=now,
        intake=intake,
        provider_results=providers,
        evidence=evidence,
        video_analyses=video_analyses,
        counterparty_lookup=lookup,
    )
    return build_analysis_input(case)


def _contains_liability_finding(output: AnalysisOutputV1) -> bool:
    terms = ("liable", "liability is established", "at fault", "fault is proven", "caused the collision")
    text = " ".join([p.text for p in output.propositions] + [i.text for i in output.contradictions]).casefold()
    return any(term in text for term in terms)


def _analyze_one(analyzer: ClaimsAnalyzer, snapshot: AnalysisInputV2) -> EvalSummary:
    output = analyzer.analyze(snapshot)
    schema_valid = True
    try:
        output = AnalysisOutputV1.model_validate(output.model_dump())
    except Exception:
        schema_valid = False
    sources_valid = False
    try:
        validate_output(output, snapshot)
        sources_valid = True
    except (AnalysisError, ValueError, TypeError):
        pass
    uncertainty_valid = all(
        proposition.assessment != "hypothesis" or bool(proposition.uncertainty_note)
        for proposition in output.propositions
    )
    uncertainty_present = any(
        proposition.assessment == "hypothesis"
        or any(term in proposition.text.casefold() for term in ("reports", "reported", "alleges", "according to"))
        for proposition in output.propositions
    )
    gates = evaluate_gates(output, snapshot)
    return EvalSummary(
        schema_valid=schema_valid,
        sources_valid=sources_valid,
        uncertainty_valid=uncertainty_valid,
        uncertainty_present=uncertainty_present,
        proposed_route=output.proposed_route,
        gate_statuses=[gate.status for gate in gates],
        recipient_present=output.recipient is not None,
        liability_claim=_contains_liability_finding(output),
        can_draft=all(gate.status == "passed" for gate in gates) and output.proposed_route == "subrogation",
    )


class GoldenAssertions(Evaluator[str, EvalSummary, dict[str, Any]]):
    """Return named deterministic assertions so case scores are visible in Logfire."""

    def evaluate(self, ctx: EvaluatorContext[str, EvalSummary, dict[str, Any]]) -> dict[str, bool]:
        expected = ctx.expected_output
        actual = ctx.output
        return {
            "schema_valid": actual.schema_valid,
            "sources_valid": actual.sources_valid,
            "uncertainty_preserved": actual.uncertainty_valid and (
                not expected["uncertainty_required"] or actual.uncertainty_present
            ),
            "expected_route": actual.proposed_route == expected["route"],
            "expected_gates": actual.gate_statuses == expected["gates"],
            "recipient_expectation": actual.recipient_present == expected["recipient_required"],
            "no_unsupported_liability_finding": not (
                expected["liability_claim_forbidden"] and actual.liability_claim
            ),
            "draft_gate_expectation": actual.can_draft == expected["can_draft"],
        }

    def get_evaluator_version(self) -> str:
        return EVALUATOR_VERSION


def build_dataset(
    analyzer: ClaimsAnalyzer | None = None,
) -> tuple[Dataset[str, EvalSummary, dict[str, Any]], Any]:
    """Build a local-only dataset. The default adapter never contacts providers or models."""
    runner_analyzer = analyzer or FixtureClaimsAnalyzer()
    dataset_spec = _load_golden()
    snapshots = {spec["id"]: _case_input(spec) for spec in dataset_spec["cases"]}
    cases = []
    for spec in dataset_spec["cases"]:
        blocked = any(status != "passed" for status in spec["expected_gates"])
        expected = {
            "route": spec["expected_route"],
            "gates": spec["expected_gates"],
            "recipient_required": spec["recipient_required"],
            "liability_claim_forbidden": spec["liability_claim_forbidden"],
            "uncertainty_required": spec.get("uncertainty_required", False),
            "can_draft": not blocked and spec["expected_route"] == "subrogation",
        }
        cases.append(Case(
            name=spec["id"],
            inputs=spec["id"],
            expected_output=expected,
        ))

    def task(case_id: str) -> EvalSummary:
        return _analyze_one(runner_analyzer, snapshots[case_id])

    return Dataset[str, EvalSummary, dict[str, Any]](
        name=f"{dataset_spec['dataset_id']}-v{dataset_spec['version']}",
        cases=cases,
        evaluators=[GoldenAssertions()],
    ), task


def run(*, upload_to_logfire: bool = False, analyzer: ClaimsAnalyzer | None = None) -> tuple[Any, dict[str, Any]]:
    dataset, task = build_dataset(analyzer)
    run_id = str(uuid4())
    experiment_name = f"claims-analysis-golden-v2-{run_id[:8]}"
    metadata = {
        "dataset_id": "claims-analysis-golden",
        "dataset_version": 2,
        "run_id": run_id,
        "analysis_method_version": getattr(analyzer, "method_version", FixtureClaimsAnalyzer.method_version),
        "analysis_mode": getattr(analyzer, "mode", "mock"),
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
                service_name="claims-analysis-evals",
                service_version="golden-v2",
                environment=os.environ.get("LOGFIRE_ENVIRONMENT", "eval"),
            )
        except Exception as exc:
            raise RuntimeError(f"Could not configure Logfire upload: {type(exc).__name__}: {exc}") from exc

    report = dataset.evaluate_sync(
        task,
        name=experiment_name,
        metadata=metadata,
        progress=False,
    )
    results = []
    passed = 0
    for case in report.cases:
        assertions = {name: bool(item.value) for name, item in case.assertions.items()}
        case_passed = bool(assertions) and all(assertions.values())
        passed += int(case_passed)
        results.append({"case_id": case.name, "passed": case_passed, "scores": assertions})
    summary = {
        **metadata,
        "dataset_name": dataset.name,
        "experiment_name": experiment_name,
        "total_cases": len(results),
        "passed_cases": passed,
        "failed_cases": len(results) - passed,
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
    parser = argparse.ArgumentParser(description="Run the deterministic claims-analysis golden evaluation.")
    parser.add_argument(
        "--upload-to-logfire",
        action="store_true",
        help="Upload Pydantic Evals experiment and case scores to the configured Logfire project.",
    )
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
