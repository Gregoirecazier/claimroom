from __future__ import annotations

from dataclasses import replace

import pytest

from claim_api.mock_insurance import insurance_lookup as real_insurance_lookup
from evals import insurance_runner


def test_pr20_insurance_probes_run_in_pydantic_evals_without_upload():
    report, summary = insurance_runner.run()

    assert report.name == summary["experiment_name"]
    assert summary["dataset_name"] == "claims-insurance-probes-v2"
    assert summary["fixture_version"] == "mock-insurance-v2"
    assert summary["fixture_sha256"] == insurance_runner._load_probes()[1]
    assert summary["uploaded_to_logfire"] is False
    assert summary["total_cases"] == summary["passed_cases"] == 20
    assert summary["failed_cases"] == 0
    assert summary["assertion_pass_rate"] == 1.0

    by_id = {case["probe_id"]: case for case in summary["case_results"]}
    assert {"g1_fr_active", "g1_uk_active", "g2_fr_active", "g2_uk_active", "g3_fr_active", "g3_uk_active"} <= by_id.keys()
    assert by_id["g2_uk_partial"]["passed"]
    assert by_id["g2_uk_alternative_absent"]["passed"]
    assert "expected_correspondent_identity" in by_id["g2_uk_active"]["scores"]
    assert "expected_correspondent_status" in by_id["uk_no_correspondent"]["scores"]


def test_probe_task_does_not_receive_reference_answers():
    dataset, _, _ = insurance_runner.build_dataset()
    for case in dataset.cases:
        assert isinstance(case.inputs, str)
        assert "plate" not in case.expected_output
        assert "insurer_name" not in case.expected_output
        assert "correspondent_name" not in case.expected_output


def test_ambiguous_g2_probe_fails_if_lookup_claims_a_match(monkeypatch: pytest.MonkeyPatch):
    def false_positive(plate, country, incident_date):
        result = real_insurance_lookup(plate, country, incident_date)
        if plate == "RK18 L?P":
            return replace(result, status="matched", reason=None, data={**result.data, "valid_on_incident_date": True, "insurer_id": "fake"})
        return result

    monkeypatch.setattr(insurance_runner, "insurance_lookup", false_positive)
    _, summary = insurance_runner.run()
    by_id = {case["probe_id"]: case for case in summary["case_results"]}
    assert summary["failed_cases"] == 1
    assert by_id["g2_uk_partial"]["passed"] is False


def test_requested_insurance_logfire_upload_requires_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="LOGFIRE_TOKEN is unset or empty"):
        insurance_runner.run(upload_to_logfire=True)
