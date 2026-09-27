"""Cost-free S16 inventory and deterministic business-port probes.

The 42 matrix rows are deliberately separate from the existing analysis and
lookup probes. A passing probe is never promoted to a matrix verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from evals import insurance_runner, runner


ROOT = Path(__file__).resolve().parent
API_ROOT = ROOT.parent
REPO_ROOT = API_ROOT.parent.parent
DATASET_PATH = ROOT / "matrix_cases_v1.json"
MEDIA_PATH = ROOT / "media_v1.json"
ANSWERS_PATH = ROOT / "reference" / "answers_v1.json"
EXPECTED_AGENT_IDS = {
    *(f"V{i:02}" for i in range(1, 7)),
    *(f"S{i:02}" for i in range(1, 4)),
    *(f"I{i:02}" for i in range(1, 7)),
    *(f"N{i:02}" for i in range(1, 7)),
    *(f"L{i:02}" for i in range(1, 4)),
    *(f"C{i:02}" for i in range(1, 3)),
}
EXPECTED_UX_IDS = {
    "S04", "S05",
    *(f"U{i:02}" for i in range(1, 8)),
    *(f"A{i:02}" for i in range(1, 8)),
}
EXPECTED_MEDIA_IDS = {
    f"{scene}-{kind}"
    for scene in ("g1", "g2", "g3")
    for kind in ("video", "photo-wide", "photo-detail")
}


class InputRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    id: str


class MatrixCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eval_id: str
    matrix: str
    scenario_id: str
    scene_group_id: str
    fixture_version: str
    app_commit: str | None
    pipeline_fingerprint: str | None
    model_version: str | None
    prompt_version: str | None
    input_refs: list[InputRef]
    expected_refs: list[str]
    status: str


class MediaRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scene_group_id: str


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_inventory() -> tuple[list[MatrixCase], dict[str, MediaRef], dict[str, str]]:
    """Validate references, without putting the correction text in task inputs."""
    dataset = _json(DATASET_PATH)
    media_data = _json(MEDIA_PATH)
    answers_data = _json(ANSWERS_PATH)
    if (dataset.get("dataset_id"), dataset.get("version")) != ("s16-matrix", 1):
        raise ValueError("Unexpected S16 matrix dataset version.")
    if (media_data.get("dataset_id"), media_data.get("version")) != ("s16-media", 1):
        raise ValueError("Unexpected S16 media dataset version.")
    if (answers_data.get("dataset_id"), answers_data.get("version")) != ("s16-matrix-reference", 1):
        raise ValueError("Unexpected S16 reference dataset version.")

    cases = [MatrixCase.model_validate(item) for item in dataset["cases"]]
    media = {item.id: item for item in (MediaRef.model_validate(raw) for raw in media_data["media"])}
    answers = {item["eval_id"]: item["expected_summary"] for item in answers_data["answers"]}
    agent_ids = {case.eval_id for case in cases if case.matrix == "agents"}
    ux_ids = {case.eval_id for case in cases if case.matrix == "ux"}
    all_ids = [case.eval_id for case in cases]
    if (len(cases) != 42 or len(set(all_ids)) != 42 or agent_ids != EXPECTED_AGENT_IDS
            or ux_ids != EXPECTED_UX_IDS or len(answers) != len(answers_data["answers"])
            or set(answers) != set(all_ids)):
        raise ValueError("S16 matrix must contain exactly the 26 agent and 16 UX IDs and references.")
    if len(media) != len(media_data["media"]) or set(media) != EXPECTED_MEDIA_IDS:
        raise ValueError("S16 requires exactly nine distinct G1–G3 media IDs.")
    for case in cases:
        if case.status != "planned" or case.expected_refs != [case.eval_id]:
            raise ValueError(f"Invalid status or correction reference for {case.eval_id}.")
        if not case.input_refs or case.input_refs[0].kind != "scenario" or case.input_refs[0].id != case.eval_id:
            raise ValueError(f"Missing scenario input for {case.eval_id}.")
        for ref in case.input_refs:
            if ref.kind not in {"scenario", "media"}:
                raise ValueError(f"Unsupported input reference in {case.eval_id}.")
            if ref.kind == "media" and ref.id not in media and (case.eval_id, ref.id) != ("L02", "g1-after-impact-missing"):
                raise ValueError(f"Unknown media reference {ref.id} in {case.eval_id}.")
            if ref.kind == "media" and ref.id in media and media[ref.id].scene_group_id != case.scene_group_id:
                raise ValueError(f"Cross-scene media reference in {case.eval_id}.")
    media_hashes: dict[str, str] = {}
    for ref in media.values():
        relative = Path(ref.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe media path for {ref.id}.")
        path = (API_ROOT / relative).resolve()
        if not path.is_file() or not path.is_relative_to(API_ROOT):
            raise ValueError(f"Missing or unsafe media file for {ref.id}.")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != ref.sha256:
            raise ValueError(f"Checksum mismatch for {ref.id}.")
        media_hashes[ref.id] = digest
    # Only IDs and checksums leave this function. The reference answers are not
    # returned and cannot be passed accidentally to a task/agent adapter.
    return cases, media, media_hashes


def task_inputs(case: MatrixCase, media: dict[str, MediaRef]) -> dict[str, Any]:
    """Allowlisted inputs for a future evaluated task; no expected_refs/corrigés."""
    missing = [ref.id for ref in case.input_refs if ref.kind == "media" and ref.id not in media]
    if missing:
        raise ValueError(f"Cannot execute {case.eval_id}: missing media fixture {', '.join(missing)}.")
    return {
        "eval_id": case.eval_id,
        "scenario_id": case.scenario_id,
        "scene_group_id": case.scene_group_id,
        "input_refs": [
            {"kind": ref.kind, "id": ref.id}
            if ref.kind == "scenario" else
            {"kind": "media", "id": ref.id, "sha256": media[ref.id].sha256}
            for ref in case.input_refs
        ],
    }


def _commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _subsuite(name: str, summary: dict[str, Any], case_key: str) -> dict[str, Any]:
    return {
        "name": name,
        "dataset_name": summary["dataset_name"],
        "fixture_sha256": summary.get("fixture_sha256"),
        "passed_cases": summary["passed_cases"],
        "failed_cases": summary["failed_cases"],
        "case_results": [
            {"id": item[case_key], "passed": item["passed"], "scores": item["scores"]}
            for item in summary["case_results"]
        ],
        "matrix_verdicts_awarded": 0,
    }


def run_offline(*, now: datetime | None = None, run_id: str | None = None) -> dict[str, Any]:
    cases, media, media_hashes = load_inventory()
    started = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    selected_run_id = run_id or str(uuid4())
    _, analysis = runner.run(upload_to_logfire=False)
    _, insurance = insurance_runner.run(upload_to_logfire=False)
    results = []
    for case in cases:
        reason = "ux_flow_not_run_offline" if case.matrix == "ux" else "agent_not_run_offline"
        if any(ref.id == "g1-after-impact-missing" for ref in case.input_refs):
            reason = "after_impact_fixture_missing"
        results.append({
            "eval_id": case.eval_id,
            "run_id": selected_run_id,
            "mode": "offline",
            "started_at": started,
            "duration_ms": 0,
            "actual_summary": None,
            "source_checks": [],
            "gate_checks": [],
            "verdict": "not_tested",
            "reason_codes": [reason],
            "trace_ref": None,
            "cost_units": 0,
        })
    subsuites = [
        _subsuite("analysis_golden", analysis, "case_id"),
        _subsuite("mock_insurance_probes", insurance, "probe_id"),
    ]
    case_lookup = {case.eval_id: case.matrix for case in cases}
    by_matrix = {
        name: {
            verdict: sum(r["verdict"] == verdict for r in results if case_lookup[r["eval_id"]] == name)
            for verdict in ("passed", "failed", "not_tested", "not_applicable")
        }
        for name in ("agents", "ux")
    }
    return {
        "schema_version": 1,
        "dataset_id": "s16-matrix",
        "dataset_version": 1,
        "dataset_sha256": hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
        "reference_sha256": hashlib.sha256(ANSWERS_PATH.read_bytes()).hexdigest(),
        "run_id": selected_run_id,
        "started_at": started,
        "mode": "offline",
        "app_commit": _commit(),
        "media_sha256": media_hashes,
        "by_matrix": by_matrix,
        "sub_suites": subsuites,
        "blocking_failure": any(suite["failed_cases"] for suite in subsuites),
        "results": results,
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# S16 offline evaluation report", "",
        f"Run: `{report['run_id']}` · {report['started_at']} · `{report['app_commit'][:12]}`", "",
        "| Matrix | Passed | Failed | Not tested | Not applicable |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, counts in report["by_matrix"].items():
        lines.append(f"| {name} | {counts['passed']} | {counts['failed']} | {counts['not_tested']} | {counts['not_applicable']} |")
    lines += ["", "## Deterministic sub-suites (not matrix verdicts)", ""]
    for suite in report["sub_suites"]:
        lines.append(f"- `{suite['name']}`: {suite['passed_cases']} passed, {suite['failed_cases']} failed; 0 matrix verdicts awarded.")
    lines += ["", "## Matrix rows", "", "| ID | Matrix | Verdict | Reason |", "| --- | --- | --- | --- |"]
    for result in report["results"]:
        matrix = "agents" if result["eval_id"] in EXPECTED_AGENT_IDS else "ux"
        lines.append(f"| {result['eval_id']} | {matrix} | {result['verdict']} | {', '.join(result['reason_codes'])} |")
    lines += ["", "Real S08 calls, model quality, UI actions, gate 4, idempotence and revisions require separate evidence and retain `not_tested` verdicts here.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run cost-free S16 inventory and deterministic API-port probes.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args(argv)
    try:
        report = run_offline()
    except (ValueError, OSError, KeyError) as exc:
        print(f"s16: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", report["started_at"])[:14]
    basename = f"s16-offline-{stamp}-{report['run_id'][:8]}"
    json_path = args.output_dir / f"{basename}.json"
    md_path = args.output_dir / f"{basename}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "by_matrix": report["by_matrix"], "blocking_failure": report["blocking_failure"]}, sort_keys=True))
    return 1 if report["blocking_failure"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
