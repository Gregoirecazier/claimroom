"""Exercise the deployed synthetic handler flow without logging credentials or case content."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing production smoke setting: {name}")
    return value


def request(url: str, *, method: str = "GET", payload: dict | None = None,
            headers: dict[str, str] | None = None, timeout: int = 90) -> tuple[int, bytes]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.status, response.read(2_000_000)
    except HTTPError as error:
        raise RuntimeError(f"{method} {req.full_url.split('?')[0]} returned HTTP {error.code}") from None
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"{method} {req.full_url.split('?')[0]} failed: {type(error).__name__}") from None


def json_request(url: str, *, method: str = "GET", payload: dict | None = None,
                 headers: dict[str, str] | None = None, expected: int = 200,
                 timeout: int = 90) -> dict:
    status, body = request(url, method=method, payload=payload, headers=headers, timeout=timeout)
    if status != expected:
        raise RuntimeError(f"{method} {url.split('?')[0]} returned HTTP {status}; expected {expected}")
    result = json.loads(body)
    if not isinstance(result, dict):
        raise RuntimeError("Production endpoint returned a non-object JSON response")
    return result


def main() -> None:
    api = require("API_PRODUCTION_URL").rstrip("/")
    web = require("WEB_PRODUCTION_URL").rstrip("/")
    voice_bridge = require("VOICE_BRIDGE_PRODUCTION_URL").rstrip("/")
    supabase = require("SUPABASE_URL").rstrip("/")
    publishable_key = require("SUPABASE_PUBLISHABLE_KEY")
    email = require("DEMO_HANDLER_EMAIL")
    password = require("DEMO_HANDLER_PASSWORD")

    if json_request(f"{api}/health/live") != {"status": "ok"}:
        raise RuntimeError("Production API health response is not status=ok")
    if json_request(f"{voice_bridge}/health/live", timeout=15) != {"status": "ok"}:
        raise RuntimeError("Production voice bridge health response is not status=ok")
    web_status, web_html = request(f"{web}/", headers={"Accept": "text/html"})
    if web_status != 200 or b"<title>Claimroom" not in web_html:
        raise RuntimeError("Production website did not serve the Claimroom app shell")

    auth = json_request(
        f"{supabase}/auth/v1/token?grant_type=password",
        method="POST", payload={"email": email, "password": password},
        headers={"apikey": publishable_key},
    )
    token = auth.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Supabase password sign-in returned no access token")
    bearer = {"Authorization": f"Bearer {token}"}
    me = json_request(f"{api}/v1/me", headers=bearer)
    if not me.get("id"):
        raise RuntimeError("Authenticated handler identity is missing")

    case = json_request(
        f"{api}/v1/cases", method="POST", payload={"scenario_id": "complete"},
        headers=bearer, expected=201,
    )
    case_id = case.get("id")
    if not case_id or not case.get("synthetic") or not case.get("provider_results"):
        raise RuntimeError("Synthetic case was not created with mock provider results")
    case_url = f"{api}/v1/cases/{case_id}"
    analysis = json_request(
        f"{case_url}/analysis-runs", method="POST",
        payload={"expected_state_version": case["state_version"]}, headers=bearer,
    )
    run = analysis.get("analysis_run") or {}
    case = analysis.get("case") or {}
    if run.get("status") != "ready" or run.get("mode") != "live":
        raise RuntimeError(f"Live analysis failed (status={run.get('status')}, code={run.get('error_code')})")
    gates = run.get("gate_results") or case.get("gate_results") or []
    counterparty = next((gate for gate in gates if gate.get("gate") == "counterparty"), None)
    if counterparty is None or counterparty.get("status") != "blocked" or \
            "corroborated_vehicle_association_required" not in counterparty.get("reason_codes", []):
        raise RuntimeError("Uncorroborated synthetic counterparty was not blocked by Gate 2")
    if case.get("current_draft") or case.get("approval") or case.get("actions"):
        raise RuntimeError("Blocked analysis created a draft, approval, or outgoing action")
    persisted = json_request(case_url, headers=bearer)
    if persisted.get("current_draft") or persisted.get("approval") or persisted.get("actions"):
        raise RuntimeError("Gate 2 block was not persisted on the synthetic case")
    print("Production smoke passed: voice bridge, public web, authenticated API, live analysis, and enforced Gate 2 block.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"Production smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
