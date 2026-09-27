"""Offline release scenarios, including real Git diffs and mocked Actions history."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import plan_production as plan


class SelectionTests(unittest.TestCase):
    def selected(self, *paths: str) -> set[str]:
        return {name for name, enabled in plan.select_components(list(paths)).items() if enabled}

    def test_component_boundaries(self):
        cases = [
            ("apps/web/src/G1App.tsx", {"web"}),
            ("apps/web/package-lock.json", {"web"}),
            ("apps/api/claim_api/routes/voice.py", {"api"}),
            ("apps/api/claim_api/vapi_native.py", {"api"}),
            ("apps/api/claim_api/migrations/versions/new.py", {"migrate", "api"}),
            ("apps/api/claim_api/migrations/data/data.json.gz", {"migrate", "api"}),
            ("apps/api/alembic.ini", {"migrate", "api"}),
            ("apps/api/uv.lock", {"migrate", "api", "vapi"}),
            ("config/prompts/voice-intake/v20.yaml", {"vapi", "api"}),
            ("config/vapi/tool.json", {"vapi", "api"}),
            (".github/scripts/sync_vapi_voice.py", {"vapi", "api"}),
            ("apps/voice-bridge/media_worker.py", {"bridge"}),
            ("apps/voice-bridge/Dockerfile", {"bridge"}),
            ("apps/api/claim_api/gradium_bridge.py", {"bridge", "api"}),
            ("railway.json", {"bridge"}),
            (".dockerignore", {"bridge"}),
            ("config/claimroom-voice-bridge.version", {"bridge"}),
            ("config/prompts/claims-analysis/v3.yaml", {"api"}),
            (".github/scripts/deploy_vercel.sh", {"api", "web"}),
            (".github/workflows/production-cd.yml", set(plan.COMPONENTS)),
            (".github/scripts/plan_production.py", set(plan.COMPONENTS)),
            ("new-shared-runtime.json", set(plan.COMPONENTS)),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(self.selected(path), expected)

    def test_docs_tests_and_empty_diff_do_not_deploy(self):
        self.assertEqual(self.selected("docs/deployment.md", "README.md", "apps/api/tests/test_voice.py",
                                      "apps/web/src/G1App.test.tsx", ".github/scripts/test_plan_production.py"), set())
        self.assertEqual(self.selected(), set())

    def test_multiple_components_and_manual_override(self):
        self.assertEqual(self.selected("apps/web/src/G1App.tsx", "config/vapi/assistant.json"),
                         {"web", "vapi", "api"})
        self.assertTrue(all(plan.select_components([], force=True).values()))


class HistoryTests(unittest.TestCase):
    sha = "a" * 40

    def run_record(self, run_id=1, conclusion="success", sha=None):
        return {"id": run_id, "conclusion": conclusion, "head_sha": sha or self.sha,
                "updated_at": "2026-09-26T10:00:00Z"}

    def test_unauthorized_and_noop_runs_are_not_checkpoints(self):
        def get(resource):
            if "/runs?" in resource:
                return {"workflow_runs": [self.run_record(4), self.run_record(3),
                                          self.run_record(2), self.run_record(1)]}
            completed = "/1/jobs?" in resource
            return {"jobs": [{"name": plan.SMOKE_JOB, "conclusion": "success" if completed else "skipped"}]}
        with patch.object(plan.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertEqual(plan.release_base(self.sha, "4", get), self.sha)

    def test_failed_cancelled_or_running_release_requires_full_repair(self):
        # A failed run may already have promoted the API. Even if a later commit
        # reverts that code, diffing only against the successful tree is unsafe.
        for conclusion in ("failure", "cancelled", "timed_out", None):
            with self.subTest(conclusion=conclusion):
                get = lambda _: {"workflow_runs": [self.run_record(conclusion=conclusion)]}
                self.assertIsNone(plan.release_base(self.sha, "2", get))

    def test_non_ancestor_or_missing_commit_requires_full_release(self):
        def get(resource):
            if "/runs?" in resource:
                return {"workflow_runs": [self.run_record()]}
            return {"jobs": [{"name": plan.SMOKE_JOB, "conclusion": "success"}]}
        for code in (1, 128):
            with patch.object(plan.subprocess, "run", return_value=subprocess.CompletedProcess([], code)):
                self.assertIsNone(plan.release_base(self.sha, "2", get))

    def test_history_pagination(self):
        resources = []
        def get(resource):
            resources.append(resource)
            if "/runs?" in resource:
                return {"workflow_runs": [self.run_record(i) for i in range(100)] if resource.endswith("&page=1")
                        else [self.run_record(101)]}
            return {"jobs": [{"name": plan.SMOKE_JOB,
                              "conclusion": "success" if "/101/jobs?" in resource else "skipped"}]}
        with patch.object(plan.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertEqual(plan.release_base(self.sha, "1000", get), self.sha)
        self.assertTrue(any("page=2" in resource for resource in resources))

    def test_old_run_reexecuted_later_is_the_actual_checkpoint(self):
        newest_created = self.run_record(2)
        rerun = {**self.run_record(1, sha="b" * 40), "updated_at": "2026-09-26T12:00:00Z"}
        def get(resource):
            if "/runs?" in resource:
                return {"workflow_runs": [newest_created, rerun]}
            return {"jobs": [{"name": plan.SMOKE_JOB, "conclusion": "success"}]}
        with patch.object(plan.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertEqual(plan.release_base(self.sha, "3", get), "b" * 40)

    def test_truncated_history_selects_full_release(self):
        self.assertIsNone(plan.release_base(self.sha, "1001", lambda _: {
            "workflow_runs": [self.run_record(i) for i in range(100)]}))

    def test_no_history(self):
        self.assertIsNone(plan.release_base(self.sha, "2", lambda _: {"workflow_runs": []}))

    def test_main_outputs_and_api_failure_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            summary = Path(directory) / "summary"
            env = {"GITHUB_SHA": self.sha, "GITHUB_RUN_ID": "2", "FORCE_ALL": "false",
                   "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}
            with patch.dict(os.environ, env), patch.object(plan, "release_base", side_effect=URLError("offline")):
                plan.main()
            self.assertIn("vapi=true", output.read_text())
            self.assertIn("any=true", output.read_text())
            self.assertIn("history unavailable", summary.read_text())
            output.write_text("")
            with patch.dict(os.environ, env), patch.object(plan, "release_base", return_value=self.sha), \
                    patch.object(plan, "changed_paths", return_value=["apps/web/src/G1App.tsx"]):
                plan.main()
            self.assertIn("web=true", output.read_text())
            self.assertIn("vapi=false", output.read_text())
            output.write_text("")
            with patch.dict(os.environ, {**env, "FORCE_ALL": "true"}), \
                    patch.object(plan, "release_base") as history:
                plan.main()
                history.assert_not_called()
            self.assertIn("bridge=true", output.read_text())


class GitDiffTests(unittest.TestCase):
    def test_accumulated_changes_deletions_and_cross_component_renames(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args], text=True).strip()
            def write(path, value):
                target = Path(directory) / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value)
            git("init", "-q")
            git("config", "user.name", "Offline test")
            git("config", "user.email", "test@example.invalid")
            write("apps/web/moved.ts", "unchanged content\n")
            write("config/vapi/tool.json", "{}")
            git("add", ".")
            git("commit", "-qm", "last successful deployment")
            base = git("rev-parse", "HEAD")
            write("apps/api/claim_api/routes/voice.py", "# unpublished change")
            git("add", ".")
            git("commit", "-qm", "author-gated change")
            (Path(directory) / "apps/voice-bridge").mkdir()
            git("mv", "apps/web/moved.ts", "apps/voice-bridge/moved.py")
            git("rm", "config/vapi/tool.json")
            git("commit", "-qm", "next authorized release")
            real_run = subprocess.run
            with patch.object(plan.subprocess, "run", side_effect=lambda *a, **kw: real_run(*a, cwd=directory, **kw)):
                paths = plan.changed_paths(base, git("rev-parse", "HEAD"))
            self.assertEqual(set(paths), {"apps/web/moved.ts", "apps/voice-bridge/moved.py",
                                          "config/vapi/tool.json", "apps/api/claim_api/routes/voice.py"})
            self.assertEqual({k for k, v in plan.select_components(paths).items() if v},
                             {"vapi", "api", "bridge", "web"})


if __name__ == "__main__":
    unittest.main()
