"""Always-on HTTP/WebSocket entry point for the Vapi–Gradium audio bridge."""

from __future__ import annotations

import os
import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException

from gradium_bridge import router
from media_worker import run as run_media_worker


@asynccontextmanager
async def lifespan(app):
    worker = asyncio.create_task(run_media_worker())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(
    title="Claimroom voice bridge", docs_url=None, redoc_url=None, lifespan=lifespan
)
app.include_router(router)


@app.get("/health")
@app.get("/health/live")
def health() -> dict[str, str]:
    required = ("GRADIUM_API_KEY", "GRADIUM_VOICE_ID", "VAPI_AUDIO_SECRET")
    if os.getenv("GRADIUM_BRIDGE_ENABLED", "").lower() not in {
        "true",
        "1",
        "yes",
    } or any(not os.getenv(name) for name in required):
        raise HTTPException(503, detail="bridge_unconfigured")
    return {"status": "ok"}
