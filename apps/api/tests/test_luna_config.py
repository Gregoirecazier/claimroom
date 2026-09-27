"""Exercise the actual Pipelex-to-OpenAI request with a fake HTTP transport."""

import os
from pathlib import Path
import subprocess
import sys


def test_luna_synthesis_sends_compatible_structured_responses_request():
    # Isolate Pipelex's global singleton from the rest of the test suite.
    script = r"""
import json
import httpx2 as httpx
from claim_api.analysis import PipelexClaimsAnalyzer, FixtureClaimsAnalyzer, build_analysis_input
from test_analysis import make_case
snapshot = build_analysis_input(make_case())
expected = FixtureClaimsAnalyzer().analyze(snapshot).model_dump(mode="json")
requests = []
async def fake_send(self, request, **kwargs):
    assert request.url.host == "api.openai.com" and request.url.path == "/v1/responses"
    body = json.loads(request.content)
    requests.append(body)
    assert body["model"] == "gpt-6-luna"
    assert body["reasoning"] == {"effort": "none"}
    assert body["temperature"] == 0 and body["max_output_tokens"] == 3000
    tool = body["tools"][0]
    return httpx.Response(200, request=request, json={
        "id": "resp_test", "object": "response", "created_at": 0,
        "model": "gpt-6-luna", "status": "completed", "parallel_tool_calls": False,
        "output": [{"type": "function_call", "id": "fc_test", "call_id": "call_test",
                    "name": tool["name"], "arguments": json.dumps(expected), "status": "completed"}],
        "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
    })
httpx.AsyncClient.send = fake_send
result = PipelexClaimsAnalyzer().analyze(snapshot)
assert result.model_dump(mode="json") == expected
assert len(requests) == 1
print("Luna structured request verified")
"""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True,
        text=True, timeout=45, env={**os.environ, "OPENAI_API_KEY": "offline-test-key",
                                  "LOGFIRE_TOKEN": "", "PYTHONPATH": f"{root}:{root / 'tests'}"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Luna structured request verified" in result.stdout
