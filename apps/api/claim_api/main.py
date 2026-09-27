from __future__ import annotations

import os
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from claim_api.auth import CurrentUser, validate_auth_configuration
from claim_api.models import ErrorEnvelope, MeResponse
from claim_api.routes.cases import router as cases_router
from claim_api.routes.evidence import router as evidence_router
from claim_api.routes.analysis import router as analysis_router
from claim_api.routes.review import router as review_router
from claim_api.routes.video import router as video_router
from claim_api.routes.voice import router as voice_router
from claim_api.gradium_bridge import router as gradium_router
from claim_api.vapi_native import router as vapi_native_router
from claim_api.routes.vision import router as vision_router
from claim_api.routes.counterparty_lookup import router as counterparty_lookup_router
from claim_api.routes.deposit_grants import router as deposit_grants_router
from claim_api.routes.sms import router as sms_router
from claim_api.routes.cameras import router as cameras_router
from claim_api.routes.insured_portal import router as insured_portal_router
from claim_api.routes.voice_web_test import router as voice_web_test_router
from claim_api.routes.dust import router as dust_router
from claim_api.routes.garages import router as garages_router
from claim_api.routes.media_workflow import router as media_workflow_router
from claim_api.routes.accident_journey import router as accident_journey_router
from claim_api.routes.reference_data import router as reference_data_router
from claim_api.routes.fake_whatsapp import router as fake_whatsapp_router
from claim_api.routes.sms_link import manager as sms_link_manager_router, guest as sms_link_guest_router, webhook as sms_link_webhook_router


_LOGFIRE_CONFIGURED = False


def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


def create_app() -> FastAPI:
    validate_auth_configuration()

    application = FastAPI(
        title="Synthetic Claims API",
        version="0.1.0",
        description="Foundation API for the synthetic motor-claim demo.",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )

    @application.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 422: "invalid_request", 503: "service_unavailable"}
        if isinstance(exc.detail, dict):
            code = exc.detail.get("code", codes.get(exc.status_code, "http_error"))
            message = exc.detail.get("message", "The request could not be completed.")
            details = exc.detail.get("details", {})
        else:
            code = codes.get(exc.status_code, "http_error")
            message = exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
            details = {}
        envelope = ErrorEnvelope(
            error={
                "code": code,
                "message": message,
                "details": details,
                "request_id": getattr(request.state, "request_id", str(uuid4())),
            }
        )
        return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(), headers=exc.headers)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
        envelope = ErrorEnvelope(
            error={
                "code": "invalid_request",
                "message": "The request did not match the API contract.",
                "details": {},
                "request_id": getattr(request.state, "request_id", str(uuid4())),
            }
        )
        return JSONResponse(status_code=422, content=envelope.model_dump())

    @application.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/v1/me", response_model=MeResponse, tags=["identity"])
    async def me(user: CurrentUser) -> MeResponse:
        return MeResponse(id=user.id, email=user.email)

    application.include_router(cases_router)
    application.include_router(evidence_router)
    application.include_router(analysis_router)
    application.include_router(review_router)
    application.include_router(video_router)
    application.include_router(voice_router)
    application.include_router(gradium_router)
    application.include_router(vapi_native_router)
    application.include_router(vision_router)
    application.include_router(counterparty_lookup_router)
    application.include_router(deposit_grants_router)
    application.include_router(sms_router)
    application.include_router(cameras_router)
    application.include_router(insured_portal_router)
    application.include_router(voice_web_test_router)
    application.include_router(dust_router)
    application.include_router(garages_router)
    application.include_router(media_workflow_router)
    application.include_router(accident_journey_router)
    application.include_router(reference_data_router)
    application.include_router(fake_whatsapp_router)
    application.include_router(sms_link_manager_router)
    application.include_router(sms_link_guest_router)
    application.include_router(sms_link_webhook_router)

    # No Logfire transport or request attributes are enabled unless an explicit
    # server token is configured. Scrub all parsed request attributes so claim
    # text, evidence details, auth headers, and signed URLs are never captured.
    token = os.getenv("LOGFIRE_TOKEN", "").strip()
    if token:
        import logfire

        global _LOGFIRE_CONFIGURED
        if not _LOGFIRE_CONFIGURED:
            os.environ.setdefault("LOGFIRE_BASE_URL", "https://logfire-eu.pydantic.dev")
            logfire.configure(
                token=token,
                send_to_logfire=True,
                service_name="synthetic-claims-api",
                environment=os.getenv("APP_ENV", "production"),
            )
            _LOGFIRE_CONFIGURED = True
        logfire.instrument_fastapi(
            application,
            request_attributes_mapper=lambda _request, _attributes: {},
        )
    return application


app = create_app()
