"""Apply the versioned Claimroom Vapi tool and closing through the HTTP API.

Dashboard drafts are deliberately ignored. The API updates the published
assistant directly; unrelated settings are checked against the repository
snapshot before any write, so drift cannot be silently overwritten.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
ASSISTANT_ID = "f470da23-a088-4c35-8a66-0878a2974c5c"
TOOL_ID = "2813293c-1baa-4b92-8f78-d96991d7f3a6"


def request(key: str, method: str, resource: str, body: dict | None = None) -> dict:
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = Request(
        f"https://api.vapi.ai/{resource}", data=payload, method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "ClaimroomPromptDeployer/1.0"},
    )
    try:
        with urlopen(req, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Vapi {method} {resource} failed (HTTP {error.code})") from error
    except URLError as error:
        raise RuntimeError(f"Vapi {method} {resource} is unavailable") from error


def matches(current: object, desired: object, path: str = "") -> list[str]:
    """Return names of drifted fields without logging their potentially secret values."""
    if desired == {"$existing": True}:
        return []
    if desired == "${EXISTING_TOKEN}":
        return [] if isinstance(current, str) and current else [path]
    if isinstance(desired, str) and desired.endswith("?token=${EXISTING_TOKEN}"):
        if not isinstance(current, str):
            return [path]
        wanted = urlsplit(desired)
        actual = urlsplit(current)
        return [] if ((actual.scheme, actual.netloc, actual.path) ==
                      (wanted.scheme, wanted.netloc, wanted.path)
                      and parse_qs(actual.query).get("token")) else [path]
    if isinstance(desired, dict):
        if not isinstance(current, dict):
            return [path]
        problems = []
        for key, value in desired.items():
            if key not in current and value == {"$existing": True}:
                continue  # Vapi may omit write-only secrets from GET.
            problems.extend(matches(current.get(key), value, f"{path}.{key}" if path else key))
        return problems
    if isinstance(desired, list):
        if not isinstance(current, list) or len(current) != len(desired):
            return [path]
        return [item for i, (a, b) in enumerate(zip(current, desired))
                for item in matches(a, b, f"{path}[{i}]")]
    return [] if current == desired else [path]


def sync(key: str, api=request) -> list[str]:
    assistant = json.loads((ROOT / "config/vapi/assistant.json").read_text())
    tool = json.loads((ROOT / "config/vapi/tool.json").read_text())
    from deploy_vapi_prompts import load_prompts
    prompt, _ = load_prompts()
    if (assistant["firstMessage"] != prompt["first_message"]
            or assistant["model"]["messages"][0]["content"] != prompt["system_prompt"]
            or assistant["clientMessages"] != prompt["client_messages"]
            or assistant["serverMessages"] != prompt["server_messages"]):
        raise RuntimeError("Vapi snapshot and published prompt YAML disagree")
    current_assistant = api(key, "GET", f"assistant/{ASSISTANT_ID}")
    current_tool = api(key, "GET", f"tool/{TOOL_ID}")
    if current_assistant.get("id") != ASSISTANT_ID or current_tool.get("id") != TOOL_ID:
        raise RuntimeError("Vapi returned an unexpected assistant or tool")

    before_assistant = copy.deepcopy(assistant)
    before_tool = copy.deepcopy(tool)
    for field in ("endCallMessage", "voicemailMessage"):
        before_assistant.pop(field)
    before_tool.pop("messages")
    drift = matches(current_assistant, before_assistant, "assistant")
    drift += matches(current_tool, before_tool, "tool")
    if drift:
        raise RuntimeError("Unversioned Vapi drift: " + ", ".join(drift))

    changed = []
    if current_tool.get("messages") != tool["messages"]:
        api(key, "PATCH", f"tool/{TOOL_ID}", {"messages": tool["messages"]})
        changed.append("tool messages")
    assistant_patch = {}
    for field in ("endCallMessage", "voicemailMessage"):
        if current_assistant.get(field) != assistant[field]:
            assistant_patch[field] = assistant[field]
    if assistant_patch:
        api(key, "PATCH", f"assistant/{ASSISTANT_ID}", assistant_patch)
        changed.append("assistant conversation")

    if matches(api(key, "GET", f"assistant/{ASSISTANT_ID}"), assistant, "assistant"):
        raise RuntimeError("Vapi assistant verification failed")
    if matches(api(key, "GET", f"tool/{TOOL_ID}"), tool, "tool"):
        raise RuntimeError("Vapi tool verification failed")
    return changed


if __name__ == "__main__":
    api_key = os.environ.get("VAPI_PRIVATE_API_KEY", "")
    if not api_key:
        raise SystemExit("VAPI_PRIVATE_API_KEY is required")
    updates = sync(api_key)
    print("Vapi voice configuration verified" + (": " + ", ".join(updates) if updates else " (already current)"))
