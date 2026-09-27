from __future__ import annotations

import json

import pytest

from evals.runner import GOLDEN_PATH, run


def test_offline_golden_eval_has_five_passing_cases_without_upload():
    _, summary = run()

    assert summary["dataset_name"] == "claims-analysis-golden-v2"
    assert summary["dataset_version"] == 2
    assert summary["uploaded_to_logfire"] is False
    assert summary["total_cases"] == summary["passed_cases"] == 5
    assert summary["failed_cases"] == 0
    assert summary["assertion_pass_rate"] == 1.0
    assert all(
        set(case["scores"]) == {
            "schema_valid",
            "sources_valid",
            "uncertainty_preserved",
            "expected_route",
            "expected_gates",
            "recipient_expectation",
            "no_unsupported_liability_finding",
            "draft_gate_expectation",
        }
        for case in summary["case_results"]
    )


def test_golden_dataset_is_versioned_and_has_five_distinct_synthetic_cases():
    dataset = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    assert dataset["version"] == 2
    assert len(dataset["cases"]) == 5
    assert len({case["id"] for case in dataset["cases"]}) == 5
    assert {case["id"] for case in dataset["cases"]} == {
        "complete-supported-claim",
        "ambiguous-plate-blocked",
        "missing-cctv-remains-optional",
        "contradictory-coverage-date-blocked",
        "unsupported-liability-stays-reported",
    }


def test_requested_logfire_upload_requires_explicit_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="LOGFIRE_TOKEN is unset or empty"):
        run(upload_to_logfire=True)
