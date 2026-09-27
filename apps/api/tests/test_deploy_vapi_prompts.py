"""Guard the production prompt sync against unrelated assistant changes."""

import importlib.util
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "deploy_vapi_prompts", ROOT / ".github/scripts/deploy_vapi_prompts.py",
)
assert SPEC and SPEC.loader
deploy_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy_module)


def assistant_for(prompt: dict) -> dict:
    assistant = {
        "id": prompt["assistant_id"],
        "firstMessage": prompt["first_message"],
        "model": {
            "provider": "openai", "model": "gpt-4.1", "temperature": 0.2,
            "toolIds": ["existing-tool"],
            "messages": [{"role": "system", "content": prompt["system_prompt"]}],
        },
        "transcriber": {"provider": "custom-transcriber", "server": {"url": "wss://example.test"}},
        "voice": {"provider": "custom-voice", "voiceId": "existing-voice"},
    }
    if "client_messages" in prompt:
        assistant["clientMessages"] = prompt["client_messages"]
    if "server_messages" in prompt:
        assistant["serverMessages"] = prompt["server_messages"]
    if "model_tool_ids" in prompt:
        assistant["model"]["toolIds"] = prompt["model_tool_ids"]
        assistant["model"]["tools"] = prompt["model_tools"]
    for source, field in (("start_speaking_plan", "startSpeakingPlan"),
                          ("stop_speaking_plan", "stopSpeakingPlan")):
        if source in prompt:
            assistant[field] = prompt[source]
    return assistant


def test_published_prompt_is_unique_and_matches_existing_assistant() -> None:
    target, known = deploy_module.load_prompts()
    assert target["version"] == "v20"
    assert (target["first_message"], target["system_prompt"]) in known
    assert deploy_module.update_payload(assistant_for(target), target, known) is None


def test_sync_replaces_only_versioned_prompt_and_preserves_model_configuration() -> None:
    old = yaml.safe_load((ROOT / "config/prompts/voice-intake/v12.yaml").read_text())
    target, known = deploy_module.load_prompts()
    before = assistant_for(old)

    payload = deploy_module.update_payload(before, target, known)

    assert payload is not None
    assert payload["model"]["messages"][0]["content"] == target["system_prompt"]
    assert payload["model"]["toolIds"] == target["model_tool_ids"]
    assert payload["model"]["tools"] == target["model_tools"]
    assert before["model"]["toolIds"] == ["existing-tool"]
    assert payload["model"]["temperature"] == 0.2
    assert before["model"]["messages"][0]["content"] == old["system_prompt"]
    assert set(payload) == {"firstMessage", "model", "startSpeakingPlan", "stopSpeakingPlan"}
    assert before["clientMessages"] == target["client_messages"]
    assert before["serverMessages"] == target["server_messages"]


def test_sync_refuses_unversioned_vapi_prompt_drift() -> None:
    target, known = deploy_module.load_prompts()
    unexpected = assistant_for(target)
    unexpected["model"]["messages"][0]["content"] = "untracked dashboard edit"
    with pytest.raises(ValueError, match="manual reconciliation"):
        deploy_module.update_payload(unexpected, target, known)


def test_upgrade_from_v19_restores_intake_tool_and_turn_timing() -> None:
    old = yaml.safe_load((ROOT / "config/prompts/voice-intake/v19.yaml").read_text())
    target, known = deploy_module.load_prompts()
    before = assistant_for(old)
    assert before["model"]["toolIds"] == []

    payload = deploy_module.update_payload(before, target, known)

    assert payload["model"]["toolIds"] == ["2813293c-1baa-4b92-8f78-d96991d7f3a6"]
    assert payload["model"]["tools"] == old["model_tools"]
    assert payload["startSpeakingPlan"] == target["start_speaking_plan"]
    assert payload["stopSpeakingPlan"] == target["stop_speaking_plan"]
    assert "transcriber" not in payload and "voice" not in payload


@pytest.mark.parametrize("field", ["startSpeakingPlan", "stopSpeakingPlan"])
def test_deploy_rejects_timing_settings_not_applied_by_provider(monkeypatch, field) -> None:
    target, _ = deploy_module.load_prompts()
    remote = assistant_for(target)
    remote.pop(field)

    def fake_request(method, key, payload=None):
        # Simulate a successful PATCH response that silently ignores timing.
        return remote.copy()

    monkeypatch.setattr(deploy_module, "api_request", fake_request)
    with pytest.raises(ValueError, match=field):
        deploy_module.deploy("test-only-key")


def test_sync_verifies_remote_prompt_and_keeps_voice_and_transcriber(monkeypatch) -> None:
    target, _ = deploy_module.load_prompts()
    old = yaml.safe_load((ROOT / "config/prompts/voice-intake/v12.yaml").read_text())
    remote = assistant_for(old)
    calls = []

    def fake_request(method, key, payload=None):
        calls.append(method)
        if method == "PATCH":
            remote.update(payload)
        return remote.copy()

    monkeypatch.setattr(deploy_module, "api_request", fake_request)
    assert deploy_module.deploy("test-only-key") == "v20"
    assert calls == ["GET", "PATCH", "GET"]
    assert deploy_module.prompt_pair(remote) == (target["first_message"], target["system_prompt"])
    assert remote["clientMessages"] == target["client_messages"]
    assert remote["serverMessages"] == target["server_messages"]
    assert remote["transcriber"]["provider"] == "custom-transcriber"
    assert remote["voice"]["voiceId"] == "existing-voice"
