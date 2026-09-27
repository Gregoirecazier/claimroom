from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from claim_api.auth import AuthenticatedUser, get_current_user, validate_auth_configuration
from claim_api.main import create_app


def test_liveness_is_public_and_reports_request_id() -> None:
    response = TestClient(create_app()).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_identity_requires_bearer_token() -> None:
    response = TestClient(create_app()).get("/v1/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.json()["error"]["request_id"]


def test_identity_returns_verified_user_shape() -> None:
    application = create_app()

    async def authenticated_user() -> AuthenticatedUser:
        return AuthenticatedUser(id="00000000-0000-0000-0000-000000000001", email="handler@example.invalid")

    application.dependency_overrides[get_current_user] = authenticated_user
    response = TestClient(application).get("/v1/me", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json() == {"id": "00000000-0000-0000-0000-000000000001", "email": "handler@example.invalid"}


def test_local_auth_requires_private_token_and_is_blocked_in_production() -> None:
    validate_auth_configuration({"LOCAL_DEV_AUTH": "true", "LOCAL_DEV_BEARER_TOKEN": "private-test-token"})

    try:
        validate_auth_configuration({
            "LOCAL_DEV_AUTH": "true",
            "LOCAL_DEV_BEARER_TOKEN": "private-test-token",
            "APP_ENV": "production",
        })
    except RuntimeError as error:
        assert "cannot be enabled" in str(error)
    else:
        raise AssertionError("production must reject local bearer auth")

    try:
        validate_auth_configuration({"LOCAL_DEV_AUTH": "true"})
    except RuntimeError as error:
        assert "LOCAL_DEV_BEARER_TOKEN is required" in str(error)
    else:
        raise AssertionError("local auth must require a caller-provided token")


def test_local_auth_uses_only_configured_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DEV_AUTH", "true")
    monkeypatch.setenv("LOCAL_DEV_BEARER_TOKEN", "private-test-token")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("VERCEL", raising=False)
    client = TestClient(create_app())

    accepted = client.get("/v1/me", headers={"Authorization": "Bearer private-test-token"})
    rejected = client.get("/v1/me", headers={"Authorization": "Bearer wrong-token"})

    assert accepted.status_code == 200
    assert accepted.json() == {"id": "local-demo-handler", "email": "local@example.invalid"}
    assert rejected.status_code == 401


def test_supabase_token_requires_authenticated_role(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_DEV_AUTH", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://demo.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-only-secret")
    client = TestClient(create_app())
    now = datetime.now(timezone.utc)
    base_claims = {
        "sub": "00000000-0000-0000-0000-000000000001",
        "email": "handler@example.invalid",
        "aud": "authenticated",
        "iss": "https://demo.supabase.co/auth/v1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }

    accepted_token = jwt.encode({**base_claims, "role": "authenticated"}, "test-only-secret", algorithm="HS256")
    wrong_role_token = jwt.encode({**base_claims, "role": "service_role"}, "test-only-secret", algorithm="HS256")

    accepted = client.get("/v1/me", headers={"Authorization": f"Bearer {accepted_token}"})
    rejected = client.get("/v1/me", headers={"Authorization": f"Bearer {wrong_role_token}"})

    assert accepted.status_code == 200
    assert accepted.json()["id"] == base_claims["sub"]
    assert rejected.status_code == 401
