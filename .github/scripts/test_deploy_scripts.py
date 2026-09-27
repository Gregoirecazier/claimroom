"""Offline contract checks for the bounded production deployment scripts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
import production_smoke  # noqa: E402
FAKE_CLI = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import time

name = Path(sys.argv[0]).name
if name == 'curl':
    if '--output' in sys.argv:
        body = os.environ.get('FAKE_HTTP_BODY', '{"status":"ok"}')
        Path(sys.argv[sys.argv.index('--output') + 1]).write_text(body)
        print(os.environ.get('FAKE_HTTP_CODE', '200'), end='')
    else:
        print('{"status":"ok"}')
elif name == 'vercel':
    if sys.argv[1] == 'deploy':
        runtime_env = os.environ.get('VERCEL_READINESS_KIND') == 'api'
        if runtime_env and '--env' not in sys.argv:
            sys.exit(7)
        if runtime_env and sys.argv[sys.argv.index('--env') + 1] != 'VOICE_ASSISTANT_VERSION=v9':
            sys.exit(8)
        if not runtime_env and '--env' in sys.argv:
            sys.exit(9)
        if '--no-wait' in sys.argv:
            sys.exit(3)
        if os.environ['VERCEL_READINESS_KIND'] == 'api':
            expected = 'VOICE_ASSISTANT_VERSION=' + os.environ['VOICE_ASSISTANT_VERSION']
            if '--env' not in sys.argv or sys.argv[sys.argv.index('--env') + 1] != expected:
                sys.exit(4)
        elif '--env' in sys.argv:
            sys.exit(4)
        mode = os.environ.get('FAKE_VERCEL_MODE', 'success')
        if mode == 'error':
            print('Build failed', file=sys.stderr)
            sys.exit(1)
        if mode == 'timeout':
            print('Production https://claimroom-test.vercel.app', file=sys.stderr, flush=True)
            print('https://claimroom-partial.vercel.app', flush=True)
            time.sleep(3)
        print('https://claimroom-test.vercel.app')
elif name == 'railway':
    if sys.argv[1] == 'up':
        pass
    elif sys.argv[1:3] == ['deployment', 'list']:
        states = os.environ['FAKE_STATES'].split(',')
        count_file = Path(os.environ['FAKE_COUNT_FILE'])
        count = int(count_file.read_text()) if count_file.exists() else 0
        count_file.write_text(str(count + 1))
        marker = 'claimroom-cd-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_SHA']
        current = {'id': 'fake-deployment', 'status': states[min(count, len(states)-1)],
                   'createdAt': '2026-09-25T21:08:15Z',
                   'meta': {'cliMessage': marker,
                            'skippedReason': os.environ.get('FAKE_RAILWAY_SKIP_REASON'),
                            'serviceManifest': {'build': {'watchPatterns': json.loads(
                                os.environ.get('FAKE_RAILWAY_WATCH_PATTERNS', '[]'))}}}}
        deployments = [current]
        limit = sys.argv[sys.argv.index('--limit') + 1]
        hide_live = os.environ.get('FAKE_RAILWAY_HIDE_LIVE_ON_LIMIT_20') == '1' and limit == '20'
        if os.environ.get('FAKE_RAILWAY_LIVE_STATUS') and not hide_live:
            deployments.append({'id': 'previous-deployment',
                                'status': os.environ['FAKE_RAILWAY_LIVE_STATUS'],
                                'createdAt': '2026-09-25T19:52:58Z'})
        print(json.dumps(deployments))
else:
    sys.exit(2)
"""


class DeployScriptsTest(unittest.TestCase):
    def run_script(self, script: str, states: str, *, overrides: dict[str, str] | None = None
                   ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for executable in ("vercel", "railway", "curl"):
                path = root / executable
                path.write_text(FAKE_CLI)
                path.chmod(0o755)
            output = root / "github-output"
            environment = {
                **os.environ,
                "PATH": f"{root}:{os.environ['PATH']}",
                "FAKE_STATES": states,
                "FAKE_COUNT_FILE": str(root / "count"),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_RUN_ID": "1234",
                "GITHUB_SHA": "abc123",
                "VERCEL_TOKEN": "fake",
                "VERCEL_ORG_ID": "fake-org",
                "VERCEL_PROJECT_ID": "fake-project",
                "VERCEL_PUBLIC_URL": "https://public.example.com",
                "VERCEL_READINESS_KIND": "api",
                "VOICE_ASSISTANT_VERSION": "v9",
                "VERCEL_PUBLIC_POLL_SECONDS": "0",
                "VERCEL_PUBLIC_MAX_WAIT_SECONDS": "1",
                "VERCEL_DEPLOY_MAX_WAIT_SECONDS": "5",
                "RAILWAY_TOKEN": "fake",
                "RAILWAY_PROJECT_ID": "fake-project",
                "RAILWAY_ENVIRONMENT_ID": "fake-environment",
                "RAILWAY_SERVICE_ID": "fake-service",
                "RAILWAY_DEPLOY_POLL_SECONDS": "0",
                "RAILWAY_DEPLOY_MAX_WAIT_SECONDS": "5",
                "VOICE_BRIDGE_PRODUCTION_URL": "https://voice.example.com",
                **(overrides or {}),
            }
            result = subprocess.run(
                ["bash", str(SCRIPTS / script)], env=environment,
                capture_output=True, text=True, timeout=10,
            )
            return result, output.read_text() if output.exists() else ""

    def test_vercel_cli_completion_and_public_api_health_are_both_required(self) -> None:
        result, output = self.run_script("deploy_vercel.sh", "unused")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("url=https://claimroom-test.vercel.app", output)
        self.assertIn("Public API alias is healthy", result.stdout)

    def test_vercel_public_web_requires_claimroom_title(self) -> None:
        result, _ = self.run_script("deploy_vercel.sh", "unused", overrides={
            "VERCEL_READINESS_KIND": "web", "FAKE_HTTP_BODY": "<html><title>Claimroom demo</title></html>",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Public web alias serves Claimroom", result.stdout)

    def test_vercel_failed_build_and_timeout_do_not_pass_old_alias(self) -> None:
        failed, _ = self.run_script("deploy_vercel.sh", "unused", overrides={"FAKE_VERCEL_MODE": "error"})
        self.assertEqual(failed.returncode, 1)
        self.assertIn("exit 1", failed.stderr)
        timed_out, _ = self.run_script("deploy_vercel.sh", "unused", overrides={
            "FAKE_VERCEL_MODE": "timeout", "VERCEL_DEPLOY_MAX_WAIT_SECONDS": "1",
        })
        self.assertEqual(timed_out.returncode, 124, timed_out.stderr)
        self.assertIn("exit 124", timed_out.stderr)
        self.assertIn("https://claimroom-partial.vercel.app", timed_out.stdout)

    def test_vercel_unhealthy_public_alias_reports_last_http_state(self) -> None:
        result, _ = self.run_script("deploy_vercel.sh", "unused", overrides={
            "FAKE_HTTP_CODE": "503",
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("last state: HTTP 503", result.stderr)

    def test_railway_waits_for_its_own_successful_deployment(self) -> None:
        result, _ = self.run_script("deploy_railway.sh", "BUILDING,SUCCESS")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Railway voice bridge ready", result.stdout)

    def test_railway_failed_deployment_fails(self) -> None:
        result, _ = self.run_script("deploy_railway.sh", "FAILED")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAILED", result.stderr)

    def test_railway_skip_only_reuses_healthy_live_claimroom_deployment(self) -> None:
        expected = ["/apps/voice-bridge/**", "/apps/api/claim_api/gradium_bridge.py", "/railway.json"]
        settings = {"FAKE_RAILWAY_SKIP_REASON": "No changes to watched files",
                    "FAKE_RAILWAY_WATCH_PATTERNS": json.dumps(expected),
                    "FAKE_RAILWAY_LIVE_STATUS": "SUCCESS"}
        result, _ = self.run_script("deploy_railway.sh", "SKIPPED", overrides=settings)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("existing live deployment is healthy", result.stdout)
        older, _ = self.run_script("deploy_railway.sh", "SKIPPED", overrides={
            **settings, "FAKE_RAILWAY_HIDE_LIVE_ON_LIMIT_20": "1",
        })
        self.assertEqual(older.returncode, 0, older.stderr)
        self.assertIn("previous-deployment", older.stdout)
        for override, error in [
            ({"FAKE_RAILWAY_WATCH_PATTERNS": '["/harness/**"]'}, "stale watch patterns"),
            ({"FAKE_RAILWAY_SKIP_REASON": "Unknown skip"}, "unexpected reason"),
            ({"FAKE_RAILWAY_LIVE_STATUS": "REMOVED"}, "no earlier live SUCCESS"),
        ]:
            with self.subTest(override=override):
                failed, _ = self.run_script("deploy_railway.sh", "SKIPPED", overrides={**settings, **override})
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn(error, failed.stderr)

    def test_railway_watch_patterns_cover_dockerfile_sources(self) -> None:
        config = json.loads((SCRIPTS.parent.parent / "railway.json").read_text())
        patterns = config["build"]["watchPatterns"]
        self.assertEqual(patterns, ["/apps/voice-bridge/**",
                                    "/apps/api/claim_api/gradium_bridge.py", "/railway.json"])
        dockerfile = (SCRIPTS.parent.parent / "apps/voice-bridge/Dockerfile").read_text()
        self.assertIn("COPY apps/api/claim_api/gradium_bridge.py", dockerfile)
        self.assertIn("COPY apps/voice-bridge/main.py", dockerfile)

    def test_production_smoke_requires_s15_gate_block_without_side_effects(self) -> None:
        case = {"id": "synthetic-case", "synthetic": True, "provider_results": [{}],
                "state_version": 1, "current_draft": None, "approval": None, "actions": []}
        analysis = {"analysis_run": {"status": "ready", "mode": "live", "gate_results": [
            {"gate": "counterparty", "status": "blocked",
             "reason_codes": ["corroborated_vehicle_association_required"]}]}, "case": case}

        def fake_json(url: str, **kwargs):
            if url.endswith("/health/live"):
                return {"status": "ok"}
            if "/auth/v1/token" in url:
                return {"access_token": "fake"}
            if url.endswith("/v1/me"):
                return {"id": "fake-handler"}
            if url.endswith("/v1/cases"):
                return case
            if url.endswith("/analysis-runs"):
                return analysis
            if url.endswith("/v1/cases/synthetic-case"):
                return case
            raise AssertionError(f"Unexpected production smoke call: {url}")

        settings = {"API_PRODUCTION_URL": "https://api.example.com",
                    "WEB_PRODUCTION_URL": "https://web.example.com",
                    "VOICE_BRIDGE_PRODUCTION_URL": "https://voice.example.com",
                    "SUPABASE_URL": "https://supabase.example.com",
                    "SUPABASE_PUBLISHABLE_KEY": "fake", "DEMO_HANDLER_EMAIL": "handler@example.com",
                    "DEMO_HANDLER_PASSWORD": "fake"}
        with patch.dict(os.environ, settings), \
             patch.object(production_smoke, "json_request", side_effect=fake_json), \
             patch.object(production_smoke, "request", return_value=(200, b"<title>Claimroom")):
            production_smoke.main()
            analysis["analysis_run"]["gate_results"][0]["status"] = "passed"
            with self.assertRaisesRegex(RuntimeError, "Gate 2"):
                production_smoke.main()
            analysis["analysis_run"]["gate_results"][0]["status"] = "blocked"
            case["current_draft"] = {"id": "unsafe"}
            with self.assertRaisesRegex(RuntimeError, "draft"):
                production_smoke.main()


if __name__ == "__main__":
    unittest.main()
