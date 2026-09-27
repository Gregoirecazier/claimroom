"""Vapi custom STT/TTS protocol bridge to Gradium.

The optional ``voice`` dependency provides the Gradium client. No client is
constructed unless the bridge is explicitly enabled and credentials exist.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field


router = APIRouter(prefix="/v1/voice/gradium", tags=["voice-bridge"])
logger = logging.getLogger(__name__)


class BridgeModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class VoiceMessage(BridgeModel):
    type: Literal["voice-request"]
    text: str = Field(min_length=1, max_length=2000)
    sampleRate: Literal[8000, 16000, 22050, 24000, 44100]


class VoiceRequest(BridgeModel):
    message: VoiceMessage


class TranscriberStart(BridgeModel):
    type: Literal["start"] = "start"
    sampleRate: Literal[8000, 16000, 22050, 24000, 44100]
    channels: Literal[1, 2] = 2


def caller_channel(pcm: bytes, channels: int) -> bytes:
    """Extract caller channel 0 from signed little-endian interleaved PCM16."""
    if channels not in {1, 2} or len(pcm) % (channels * 2):
        raise ValueError("PCM frame must contain whole 16-bit samples")
    if channels == 1:
        return pcm
    return b"".join(pcm[index:index + 2] for index in range(0, len(pcm), 4))


def _bridge_secret() -> str:
    if os.getenv("GRADIUM_BRIDGE_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(503, detail={"code": "voice_bridge_disabled", "message": "Voice bridge disabled.", "details": {}})
    secret = os.getenv("VAPI_AUDIO_SECRET", "")
    if not secret or not os.getenv("GRADIUM_API_KEY", ""):
        raise HTTPException(503, detail={"code": "voice_bridge_unconfigured", "message": "Voice bridge not configured.", "details": {}})
    return secret


def _authorize(candidate: str | None) -> None:
    secret = _bridge_secret()
    if not candidate or not hmac.compare_digest(candidate, secret):
        raise HTTPException(401, detail={"code": "invalid_voice_credential", "message": "Invalid voice credential.", "details": {}})


def _client():
    from gradium.client import GradiumClient

    return GradiumClient(api_key=os.environ["GRADIUM_API_KEY"])


@router.post("/tts")
async def tts(request: VoiceRequest,
              x_vapi_secret: Annotated[str | None, Header(alias="X-Vapi-Secret")] = None) -> StreamingResponse:
    _authorize(x_vapi_secret)
    voice_id = os.getenv("GRADIUM_VOICE_ID", "")
    if not voice_id:
        raise HTTPException(503, detail={"code": "voice_bridge_unconfigured", "message": "Gradium voice not configured.", "details": {}})

    async def pcm_stream():
        client = _client()
        async with client.tts_realtime(
            voice_id=voice_id, output_format=f"pcm_{request.message.sampleRate}",
        ) as gradium:
            await gradium.send_text(request.message.text)
            await gradium.send_eos()
            async for message in gradium:
                if message.get("type") == "audio":
                    chunk = message["audio"]
                    if not isinstance(chunk, bytes) or len(chunk) % 2:
                        raise ValueError("Gradium returned an invalid PCM16 chunk")
                    yield chunk
                elif message.get("type") == "end_of_stream":
                    break

        # Vapi's blocking endCall message waits for playback of this PCM.
        # Match the final text chunk as well as an unchunked closing sentence.
        if request.message.text.strip().rstrip(".!… ").casefold().endswith("on se charge de votre dossier"):
            yield b"\x00\x00" * request.message.sampleRate * 2

    return StreamingResponse(pcm_stream(), media_type="application/octet-stream")


@router.websocket("/transcriber")
async def transcriber(websocket: WebSocket) -> None:
    supplied_secret = websocket.headers.get("x-vapi-secret")
    authorization = websocket.headers.get("authorization")
    if not supplied_secret and authorization:
        supplied_secret = authorization.removeprefix("Bearer ")
    url_token = websocket.query_params.get("token")
    expected_url_token = os.getenv("VAPI_TRANSCRIBER_URL_TOKEN", "")
    try:
        if not (expected_url_token and url_token and hmac.compare_digest(url_token, expected_url_token)):
            _authorize(supplied_secret)
        else:
            _bridge_secret()
    except HTTPException as error:
        logger.warning(
            "voice transcriber rejected: status=%s x_vapi_secret=%s authorization=%s header_names=%s query_keys=%s",
            error.status_code, "x-vapi-secret" in websocket.headers,
            "authorization" in websocket.headers,
            sorted(websocket.headers.keys()), sorted(websocket.query_params.keys()),
        )
        await websocket.close(code=4401 if error.status_code == 401 else 4503)
        return
    await websocket.accept()
    logger.info("voice transcriber connected")
    try:
        start = TranscriberStart.model_validate(json.loads(await websocket.receive_text()))
    except WebSocketDisconnect:
        return
    except ValueError:
        logger.warning("voice transcriber invalid start")
        await websocket.close(code=4400)
        return
    logger.info("voice transcriber started: rate=%s channels=%s", start.sampleRate, start.channels)

    client = _client()

    async def send(text: str, kind: Literal["partial", "final"]) -> None:
        await websocket.send_json({"type": "transcriber-response", "transcription": text,
                                   "channel": "customer", "transcriptType": kind})

    try:
        await _transcribe_stream(websocket, client, start, send)
    except Exception as error:
        logger.error("voice transcriber failed: %s", type(error).__name__)
        raise


async def _transcribe_stream(websocket: WebSocket, client, start: TranscriberStart, send) -> None:
    words: list[str] = []
    high_vad_steps = 0
    flush_pending = False
    async with client.stt_realtime(
        input_format=f"pcm_{start.sampleRate}",
        json_config={"language": "fr", "delay_in_frames": 16},
    ) as gradium:
        async def to_gradium() -> None:
            while True:
                try:
                    frame = await websocket.receive_bytes()
                except WebSocketDisconnect:
                    break
                await gradium.send_audio(caller_channel(frame, start.channels))

        async def to_vapi() -> None:
            nonlocal high_vad_steps, flush_pending
            async for message in gradium:
                kind = message.get("type")
                if kind == "text":
                    text = message.get("text", "").strip()
                    if text:
                        words.append(text)
                        await send(" ".join(words), "partial")
                elif kind == "step":
                    vad = message.get("vad") or []
                    probability = vad[-1].get("inactivity_prob", 0) if vad else 0
                    high_vad_steps = high_vad_steps + 1 if probability > 0.5 else 0
                    if high_vad_steps >= 3 and words and not flush_pending:
                        flush_pending = True
                        await gradium.send_flush(flush_id=1)
                elif kind == "flushed":
                    if words:
                        await send(" ".join(words), "final")
                    words.clear()
                    high_vad_steps = 0
                    flush_pending = False
                elif kind == "end_of_stream":
                    break

        tasks = [asyncio.create_task(to_gradium()), asyncio.create_task(to_vapi())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await websocket.close()
            except (RuntimeError, WebSocketDisconnect):
                pass
