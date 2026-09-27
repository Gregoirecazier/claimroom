"""Opt-in, paid smoke test of the real Pipelex/OpenAI analysis path."""

from __future__ import annotations

import argparse
import json
import os
import sys
from time import monotonic

from claim_api.analysis import AnalysisError, PipelexClaimsAnalyzer, evaluate_gates, validate_output
from evals.runner import _case_input, _load_golden


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("complete-supported-claim", "ambiguous-plate-blocked"),
                        default="complete-supported-claim")
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY", "").strip():
        print("Live smoke needs OPENAI_API_KEY in the API process environment.", file=sys.stderr)
        return 2

    spec = next(item for item in _load_golden()["cases"] if item["id"] == args.case)
    snapshot = _case_input(spec)
    started = monotonic()
    try:
        output = PipelexClaimsAnalyzer().analyze(snapshot)
        validate_output(output, snapshot)
        gates = evaluate_gates(output, snapshot)
    except AnalysisError as error:
        print(json.dumps({"status": "failed", "error_code": error.code,
                          "message": str(error), "elapsed_seconds": round(monotonic() - started, 1)}))
        return 1

    statuses = [gate.status for gate in gates]
    passed = output.proposed_route == spec["expected_route"] and statuses == spec["expected_gates"]
    print(json.dumps({"status": "passed" if passed else "unexpected_result", "case": args.case,
                      "mode": "live", "route": output.proposed_route, "gates": statuses,
                      "source_validation": "passed", "elapsed_seconds": round(monotonic() - started, 1)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
