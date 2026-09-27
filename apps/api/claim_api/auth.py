from __future__ import annotations

import hmac
import os
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    email: str | None = None


_bearer_scheme = HTTPBearer(auto_error=False)
_ALLOWED_SUPABASE_ALGORITHMS = ("ES256", "RS256")


def validate_auth_configuration(environ: dict[str, str] | None = None) -> None:
    """Reject unsafe local authentication settings before serving requests."""
    env = os.environ if environ is None else environ
    if not _enabled(env.get("LOCAL_DEV_AUTH", "false")):
        return

    if _enabled(env.get("VERCEL", "false")) or env.get("APP_ENV", "development").lower() == "production":
        raise RuntimeError("LOCAL_DEV_AUTH cannot be enabled in production or on Vercel")
    if not env.get("LOCAL_DEV_BEARER_TOKEN", "").strip():
        raise RuntimeError("LOCAL_DEV_BEARER_TOKEN is required when LOCAL_DEV_AUTH is enabled")


def _enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, timeout=3, cache_keys=True)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid bearer access token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_supabase_token(token: str, env: dict[str, str]) -> AuthenticatedUser:
    supabase_url = env.get("SUPABASE_URL", "").strip().rstrip("/")
    if not supabase_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SUPABASE_URL is required for Supabase JWT verification.",
        )
    jwks_url = env.get("SUPABASE_JWKS_URL", "").strip()
    jwt_secret = env.get("SUPABASE_JWT_SECRET", "").strip()
    if not jwt_secret and not jwks_url and supabase_url:
        jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    if not jwt_secret and not jwks_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase JWT verification is not configured.",
        )

    try:
        if jwt_secret:
            payload = jwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
                issuer=f"{supabase_url}/auth/v1",
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        else:
            signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(_ALLOWED_SUPABASE_ALGORITHMS),
                audience="authenticated",
                issuer=f"{supabase_url}/auth/v1",
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
    except HTTPException:
        raise
    except (jwt.PyJWTError, OSError, ValueError) as exc:
        raise _unauthorized() from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject or payload.get("role") != "authenticated":
        raise _unauthorized()
    email = payload.get("email")
    return AuthenticatedUser(id=subject, email=email if isinstance(email, str) else None)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> AuthenticatedUser:
    env = dict(os.environ)
    validate_auth_configuration(env)
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    token = credentials.credentials
    if _enabled(env.get("LOCAL_DEV_AUTH", "false")):
        expected = env.get("LOCAL_DEV_BEARER_TOKEN", "")
        if not expected or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
            raise _unauthorized()
        return AuthenticatedUser(id="local-demo-handler", email="local@example.invalid")

    try:
        return _decode_supabase_token(token, env)
    except HTTPException:
        raise
    except Exception as exc:  # Network/JWKS lookup failures are configuration/provider errors.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase JWT verification is temporarily unavailable.",
        ) from exc


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
