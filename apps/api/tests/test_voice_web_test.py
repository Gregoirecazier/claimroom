from __future__ import annotations

from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.main import create_app
from claim_api.routes.voice import get_voice_repository


OWNER = "00000000-0000-4000-8000-000000000843"
ORIGIN = "https://claimroom-demo-web.vercel.app"


def _client(monkeypatch, user_id=OWNER):
    monkeypatch.setenv("VOICE_OWNER_USER_ID", OWNER)
    monkeypatch.setenv("VOICE_WEB_TEST_ENABLED", "true")
    monkeypatch.setenv("CORS_ORIGINS", ORIGIN)
    monkeypatch.setenv("VAPI_PRIVATE_API_KEY", "test-private-signing-key")
    monkeypatch.setenv("VAPI_ORG_ID", "test-vapi-org")
    monkeypatch.setenv("VOICE_ASSISTANT_ID", "test-assistant")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=user_id)
    return TestClient(app)


def test_web_test_issues_short_scoped_jwt(monkeypatch):
    client = _client(monkeypatch)
    response = client.post("/v1/voice/web-test/session", headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    result = response.json()
    assert result["assistant_id"] == "test-assistant"
    payload = jwt.decode(result["token"], "test-private-signing-key", algorithms=["HS256"])
    assert payload["orgId"] == "test-vapi-org"
    assert 0 < payload["exp"] - payload["iat"] <= 300
    assert payload["token"] == {"tag": "public", "restrictions": {
        "enabled": True, "allowedOrigins": [ORIGIN],
        "allowedAssistantIds": ["test-assistant"], "allowTransientAssistant": False,
    }}
    assert "test-private-signing-key" not in response.text


def test_web_test_rejects_wrong_owner_origin_and_disabled_mode(monkeypatch):
    client = _client(monkeypatch, "00000000-0000-4000-8000-000000000999")
    assert client.post("/v1/voice/web-test/session", headers={"Origin": ORIGIN}).status_code == 403
    client = _client(monkeypatch)
    assert client.post("/v1/voice/web-test/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/v1/voice/web-test/session").status_code == 403
    monkeypatch.setenv("VOICE_WEB_TEST_ENABLED", "false")
    assert client.post("/v1/voice/web-test/session", headers={"Origin": ORIGIN}).status_code == 503


def test_web_test_call_resolution_is_owner_and_transport_scoped(monkeypatch):
    client = _client(monkeypatch)
    class Repository:
        def get_state(self, actor, call_id):
            assert actor == UUID(OWNER)
            if call_id == "browser-call":
                return {"case_id": UUID("00000000-0000-4000-8000-000000000844"),
                        "telephony_provider": "web", "triage_json": {"status": "complete"},
                        "recording_status": "available"}
            if call_id == "phone-call":
                return {"telephony_provider": "twilio"}
            return None
    client.app.dependency_overrides[get_voice_repository] = lambda: Repository()
    result = client.get("/v1/voice/web-test/calls/browser-call")
    assert result.status_code == 200
    assert result.json() == {"case_id": "00000000-0000-4000-8000-000000000844",
                             "status": "complete", "recording_status": "available"}
    assert result.headers["cache-control"] == "private, no-store"
    assert client.get("/v1/voice/web-test/calls/phone-call").status_code == 404
    assert client.get("/v1/voice/web-test/calls/%2E%2E").status_code == 404
