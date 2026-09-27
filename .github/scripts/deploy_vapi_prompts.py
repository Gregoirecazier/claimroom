"""Synchronize the published voice prompt YAML with the existing Vapi assistant."""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml


PROMPTS = Path(__file__).resolve().parents[2] / "config/prompts/voice-intake"
ASSISTANT_ID = "f470da23-a088-4c35-8a66-0878a2974c5c"
API_URL = f"https://api.vapi.ai/assistant/{ASSISTANT_ID}"


def load_prompts() -> tuple[dict, set[tuple[str, str]]]:
    versions = [yaml.safe_load(path.read_text()) for path in sorted(PROMPTS.glob("v*.yaml"))]
    published = [item for item in versions if item["status"] == "published"]
    if len(published) != 1:
        raise ValueError("Exactly one published voice prompt YAML is required")
    target = published[0]
    if (target["assistant_id"] != ASSISTANT_ID or target["provider"] != "vapi"
            or target["schema_version"] != 1 or not re.fullmatch(r"v[1-9][0-9]*", target["version"])):
        raise ValueError("Published voice prompt metadata is invalid")
    for name in ("first_message", "system_prompt"):
        if not isinstance(target[name], str) or not target[name].strip():
            raise ValueError(f"Published voice prompt has no {name}")
    for name, required in (("client_messages", "conversation-update"),
                           ("server_messages", "end-of-call-report")):
        messages = target.get(name)
        if (not isinstance(messages, list) or not messages
                or any(not isinstance(item, str) or not item for item in messages)
                or len(messages) != len(set(messages))
                or "assistant.speechStarted" not in messages or required not in messages):
            raise ValueError(f"Published voice prompt has invalid {name}")
    known = {
        (item["first_message"], item["system_prompt"])
        for item in versions if item["status"] in {"published", "published_historical"}
        and item["assistant_id"] == ASSISTANT_ID
    }
    return target, known


def prompt_pair(assistant: dict) -> tuple[str, str]:
    messages = assistant["model"]["messages"]
    if len(messages) != 1 or messages[0]["role"] != "system":
        raise ValueError("Vapi assistant has unexpected model messages")
    return assistant["firstMessage"], messages[0]["content"]


def update_payload(assistant: dict, target: dict, known: set[tuple[str, str]]) -> dict | None:
    current_pair = prompt_pair(assistant)
    target_pair = target["first_message"], target["system_prompt"]
    if current_pair != target_pair and current_pair not in known:
        raise ValueError("Vapi prompt differs from versioned history; manual reconciliation required")
    payload = {}
    model = copy.deepcopy(assistant["model"])
    model["messages"][0]["content"] = target["system_prompt"]
    for yaml_name, api_name in (("model_tool_ids", "toolIds"), ("model_tools", "tools")):
        if yaml_name in target:
            model[api_name] = copy.deepcopy(target[yaml_name])
    if model != assistant["model"]:
        payload["model"] = model
    if current_pair != target_pair:
        payload["firstMessage"] = target["first_message"]
    for yaml_name, api_name in (("client_messages", "clientMessages"),
                                ("server_messages", "serverMessages"),
                                ("start_speaking_plan", "startSpeakingPlan"),
                                ("stop_speaking_plan", "stopSpeakingPlan")):
        if assistant.get(api_name) != target[yaml_name]:
            payload[api_name] = target[yaml_name]
    return payload or None


def api_request(method: str, key: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = Request(API_URL, data=body, method=method, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "ClaimroomPromptDeployer/1.0",
    })
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Vapi {method} failed with HTTP {error.code}") from None


def deploy(key: str) -> str:
    target, known = load_prompts()
    before = api_request("GET", key)
    if before.get("id") != ASSISTANT_ID:
        raise ValueError("Vapi returned a different assistant")
    payload = update_payload(before, target, known)
    if payload is not None:
        api_request("PATCH", key, payload)
    after = api_request("GET", key)
    if prompt_pair(after) != (target["first_message"], target["system_prompt"]):
        raise ValueError("Vapi prompt verification failed")
    for yaml_name, api_name in (("client_messages", "clientMessages"),
                                ("server_messages", "serverMessages"),
                                ("start_speaking_plan", "startSpeakingPlan"),
                                ("stop_speaking_plan", "stopSpeakingPlan")):
        if after.get(api_name) != target[yaml_name]:
            raise ValueError(f"Vapi {api_name} verification failed")
    for yaml_name, api_name in (("model_tool_ids", "toolIds"), ("model_tools", "tools")):
        if yaml_name in target and after["model"].get(api_name, []) != target[yaml_name]:
            raise ValueError(f"Vapi model {api_name} verification failed")
    for field in ("transcriber", "voice"):
        if before.get(field) != after.get(field):
            raise ValueError(f"Vapi {field} changed during prompt deployment")
    print(f"Vapi voice prompt {target['version']} verified ({'updated' if payload else 'unchanged'})")
    return target["version"]


if __name__ == "__main__":
    secret = os.environ.get("VAPI_PRIVATE_API_KEY", "")
    if not secret:
        raise SystemExit("VAPI_PRIVATE_API_KEY is required for automatic prompt deployment")
    version = deploy(secret)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as handle:
            handle.write(f"version={version}\n")
