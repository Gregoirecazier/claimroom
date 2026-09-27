from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from evals import matrix_runner


def test_matrix_inventory_is_complete_and_keeps_answers_out_of_task_inputs():
    cases, media, hashes = matrix_runner.load_inventory()

    assert len(cases) == 42
    assert len(hashes) == len(media) == 9
    assert {case.eval_id for case in cases if case.matrix == "agents"} == matrix_runner.EXPECTED_AGENT_IDS
    assert {case.eval_id for case in cases if case.matrix == "ux"} == matrix_runner.EXPECTED_UX_IDS
    assert {case.scene_group_id for case in cases if case.eval_id in {"I01", "I06", "L01"}} == {"g1"}
    assert {case.scene_group_id for case in cases if case.eval_id in {"I02", "I03", "N05"}} == {"g2"}
    assert next(case.scene_group_id for case in cases if case.eval_id == "L03") == "g3"

    correction = json.loads(matrix_runner.ANSWERS_PATH.read_text())
    assert len(correction["answers"]) == 42
    answers = {item["eval_id"]: item for item in correction["answers"]}
    for case in cases:
        if case.eval_id == "L02":
            with pytest.raises(ValueError, match="missing media fixture"):
                matrix_runner.task_inputs(case, media)
            continue
        payload = json.dumps(matrix_runner.task_inputs(case, media), ensure_ascii=False)
        assert "expected_refs" not in payload
        assert "expected_summary" not in payload
        assert "scenario_note" not in payload
        assert answers[case.eval_id]["expected_summary"] not in payload
        assert answers[case.eval_id]["scenario_note"] not in payload


def test_offline_run_does_not_award_matrix_success_from_subsuites():
    report = matrix_runner.run_offline(
        now=datetime(2026, 9, 25, tzinfo=timezone.utc), run_id="example-run",
    )

    assert report["by_matrix"] == {
        "agents": {"passed": 0, "failed": 0, "not_tested": 26, "not_applicable": 0},
        "ux": {"passed": 0, "failed": 0, "not_tested": 16, "not_applicable": 0},
    }
    assert [(suite["passed_cases"], suite["failed_cases"]) for suite in report["sub_suites"]] == [(5, 0), (20, 0)]
    assert all(suite["matrix_verdicts_awarded"] == 0 for suite in report["sub_suites"])
    assert all(item["verdict"] == "not_tested" and item["reason_codes"] and item["cost_units"] == 0 for item in report["results"])
    assert next(item["reason_codes"] for item in report["results"] if item["eval_id"] == "L02") == ["after_impact_fixture_missing"]
    assert report["blocking_failure"] is False
    markdown = matrix_runner.markdown_report(report)
    assert "| agents | 0 | 0 | 26 | 0 |" in markdown
    assert "| ux | 0 | 0 | 16 | 0 |" in markdown


def test_media_checksum_mismatch_fails_closed(tmp_path, monkeypatch: pytest.MonkeyPatch):
    data = json.loads(matrix_runner.MEDIA_PATH.read_text())
    data["media"][0]["sha256"] = "0" * 64
    corrupted = tmp_path / "media.json"
    corrupted.write_text(json.dumps(data))
    monkeypatch.setattr(matrix_runner, "MEDIA_PATH", corrupted)

    with pytest.raises(ValueError, match="Checksum mismatch"):
        matrix_runner.load_inventory()


def test_missing_reference_id_fails_closed(tmp_path, monkeypatch: pytest.MonkeyPatch):
    data = json.loads(matrix_runner.ANSWERS_PATH.read_text())
    data["answers"].pop()
    incomplete = tmp_path / "answers.json"
    incomplete.write_text(json.dumps(data))
    monkeypatch.setattr(matrix_runner, "ANSWERS_PATH", incomplete)

    with pytest.raises(ValueError, match="26 agent and 16 UX"):
        matrix_runner.load_inventory()
