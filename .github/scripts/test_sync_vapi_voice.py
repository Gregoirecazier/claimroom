"""Offline checks for the production Vapi sync gate."""

import copy
import json
import unittest

import sync_vapi_voice as voice


class VoiceSyncTests(unittest.TestCase):
    def setUp(self):
        assistant = json.loads((voice.ROOT / "config/vapi/assistant.json").read_text())
        assistant["id"] = voice.ASSISTANT_ID
        assistant["voice"]["server"]["secret"] = "test-secret"
        assistant["transcriber"]["server"]["url"] = assistant["transcriber"]["server"]["url"].replace(
            "${EXISTING_TOKEN}", "test-token")
        assistant["endCallMessage"] = "Goodbye."
        tool = json.loads((voice.ROOT / "config/vapi/tool.json").read_text())
        tool["id"] = voice.TOOL_ID
        tool["messages"] = [{"type": "request-start", "blocking": False}]
        self.objects = {f"assistant/{voice.ASSISTANT_ID}": assistant,
                        f"tool/{voice.TOOL_ID}": tool}
        self.writes = []

    def api(self, _key, method, resource, body=None):
        if method == "PATCH":
            self.writes.append((resource, copy.deepcopy(body)))
            self.objects[resource].update(copy.deepcopy(body))
        return copy.deepcopy(self.objects[resource])

    def test_updates_conversation_and_tool_without_touching_voice_secrets(self):
        self.assertEqual(voice.sync("test-key", self.api), ["tool messages", "assistant conversation"])
        self.assertEqual(len(self.writes), 2)
        self.assertEqual(set(self.writes[1][1]), {"endCallMessage"})
        self.assertEqual(voice.sync("test-key", self.api), [])

    def test_unrelated_drift_blocks_all_writes(self):
        self.objects[f"assistant/{voice.ASSISTANT_ID}"]["model"]["model"] = "other-model"
        with self.assertRaisesRegex(RuntimeError, "assistant.model.model"):
            voice.sync("test-key", self.api)
        self.assertEqual(self.writes, [])

    def test_prompt_is_owned_by_the_versioned_yaml(self):
        self.objects[f"assistant/{voice.ASSISTANT_ID}"]["model"]["messages"][0]["content"] = "Unversioned edit"
        with self.assertRaisesRegex(RuntimeError, "assistant.model.messages"):
            voice.sync("test-key", self.api)
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
