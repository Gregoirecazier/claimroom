"""Select production components against the last smoke-verified release.

Unknown paths/history select everything. This is intentionally independent of
push.before: unauthorized pushes, failed releases and coalesced queue entries
must never make unpublished changes disappear from the next release.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

COMPONENTS = ("vapi", "migrate", "bridge", "api", "web")
SMOKE_JOB = "Exercise production handler flow"


def select_components(paths: list[str], *, force: bool = False) -> dict[str, bool]:
    selected = dict.fromkeys(COMPONENTS, force)
    for path in paths:
        if (path.startswith(("docs/", "apps/api/tests/", "apps/api/evals/"))
                or path.endswith((".md", ".test.ts", ".test.tsx"))
                or path in {".gitignore", "LICENSE"}
                or path.startswith(".github/scripts/test_")):
            continue
        if path.startswith(("config/prompts/voice-intake/", "config/vapi/")) or path in {
            ".github/scripts/deploy_vapi_prompts.py", ".github/scripts/sync_vapi_voice.py",
        }:
            selected["vapi"] = selected["api"] = True
        elif path.startswith("apps/voice-bridge/") or path in {
            "railway.json", ".dockerignore", "config/claimroom-voice-bridge.version",
            ".github/scripts/deploy_railway.sh",
        }:
            selected["bridge"] = True
        elif path == "apps/api/claim_api/gradium_bridge.py":
            selected["bridge"] = selected["api"] = True
        elif path.startswith("apps/api/claim_api/migrations/") or path == "apps/api/alembic.ini":
            selected["migrate"] = selected["api"] = True
        elif path in {"apps/api/pyproject.toml", "apps/api/uv.lock", "apps/api/requirements.txt"}:
            selected["api"] = selected["migrate"] = selected["vapi"] = True
        elif path.startswith(("apps/api/", "config/prompts/")):
            selected["api"] = True
        elif path.startswith("apps/web/"):
            selected["web"] = True
        elif path == ".github/scripts/deploy_vercel.sh":
            selected["api"] = selected["web"] = True
        else:
            # Workflow/planner changes and new, unclassified shared files.
            selected = dict.fromkeys(COMPONENTS, True)
    return selected


def github_get(resource: str) -> dict:
    request = Request(
        f"{os.environ['GITHUB_API_URL']}/repos/{os.environ['GITHUB_REPOSITORY']}/{resource}",
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def release_base(head: str, run_id: str, get=github_get) -> str | None:
    # The API lists by creation time, but rerunning an old release can change
    # production today. Gather bounded history and order by last update instead.
    history = []
    for page in range(1, 11):
        runs = get("actions/workflows/production-cd.yml/runs"
                   f"?branch=main&per_page=100&page={page}")["workflow_runs"]
        history.extend(runs)
        if len(runs) < 100:
            break
    else:
        return None  # An older rerun outside this window could be the latest release.
    for run in sorted(history, key=lambda item: item["updated_at"], reverse=True):
        if str(run["id"]) == run_id:
            continue
        # Even a failed/cancelled release can have changed production. A full
        # release also repairs a later revert to the previous successful tree.
        if run["conclusion"] != "success":
            return None
        jobs = get(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100")["jobs"]
        if not any(job["name"] == SMOKE_JOB and job["conclusion"] == "success" for job in jobs):
            continue  # Author-gated or no-op run is not a production checkpoint.
        sha = run["head_sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            return None
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", sha, head],
                                  capture_output=True)
        return sha if ancestor.returncode == 0 else None
    return None


def changed_paths(base: str, head: str) -> list[str]:
    # Disable rename detection so moves across component boundaries select both.
    result = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z", base, head],
                            check=True, capture_output=True)
    return [path for path in result.stdout.decode().split("\0") if path]


def main() -> None:
    head = os.environ["GITHUB_SHA"]
    force = os.environ.get("FORCE_ALL") == "true"
    base = None
    reason = "Manual full release" if force else "No trustworthy production checkpoint; full release"
    if not force:
        try:
            base = release_base(head, os.environ["GITHUB_RUN_ID"])
        except (OSError, ValueError, KeyError):
            # Do not log HTTP response bodies or credentials.
            reason = "Production history unavailable; full release"
    paths = changed_paths(base, head) if base else []
    selected = select_components(paths, force=force or base is None)
    outputs = {**selected, "any": any(selected.values())}
    with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
        for name, value in outputs.items():
            handle.write(f"{name}={str(value).lower()}\n")
    summary = ["## Production deployment plan", "",
               f"Compared `{base}` → `{head}`" if base else reason, "",
               "| Component | Action |", "| --- | --- |"]
    summary += [f"| {name} | {'Deploy' if value else 'Skip'} |" for name, value in selected.items()]
    Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text("\n".join(summary) + "\n")
    print(json.dumps({"base": base, **outputs}))


if __name__ == "__main__":
    main()
