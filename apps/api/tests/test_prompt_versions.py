"""Keep YAML prompt history aligned with mandatory runtime formats."""

from pathlib import Path
import re
from typing import get_args

import yaml

from claim_api.voice_intake import VoiceFieldName


ROOT = Path(__file__).resolve().parents[3]
PROMPTS = ROOT / "config/prompts"


def test_voice_prompt_versions_have_one_recording_announcement() -> None:
    draft = yaml.safe_load((PROMPTS / "voice-intake/v8.yaml").read_text())
    proposed = yaml.safe_load((PROMPTS / "voice-intake/v13.yaml").read_text())
    assert draft["assistant_id"] == proposed["assistant_id"]
    assert draft["first_message"] == proposed["first_message"]
    assert "cet appel est enregistré" in proposed["first_message"]
    assert "ne reparle pas de l’enregistrement" in proposed["system_prompt"]
    assert "Au début, annonce" not in proposed["system_prompt"]
    assert "tous les faits explicitement présents" in proposed["system_prompt"]
    assert "minutes_ago:10" in proposed["system_prompt"]
    assert all(field in proposed["system_prompt"] for field in get_args(VoiceFieldName))
    assert "WhatsApp" in proposed["system_prompt"]
    assert "Parle exclusivement en français" in proposed["system_prompt"]


def test_pipelex_prompt_versions_match_runtime_files() -> None:
    for version in ("v1", "v2"):
        record = yaml.safe_load((PROMPTS / f"claims-analysis/{version}.yaml").read_text())
        runtime = (ROOT / record["runtime_file"]).read_text()
        system = re.search(r'^system_prompt = "([^"]+)"$', runtime, re.M)
        assert system is not None
        assert record["system_prompt"] == system.group(1)
        assert record["prompt"].strip() == runtime.split('prompt = """', 1)[1].rsplit('"""', 1)[0].strip()


def test_fixture_prompts_have_yaml_versions() -> None:
    for group in ("g2", "g3"):
        for view in ("ensemble", "detail"):
            record = yaml.safe_load((PROMPTS / f"fixture-media/{group}-{view}-v1.yaml").read_text())
            source = (ROOT / record["source_file"]).read_text().split("\n\n", 2)[2]
            assert record["prompt"].strip() == source.strip()
