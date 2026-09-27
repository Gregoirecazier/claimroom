from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from claim_api import gradium_bridge
from claim_api.main import create_app


def _configured(monkeypatch) -> None:
    monkeypatch.setenv("GRADIUM_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("VAPI_AUDIO_SECRET", "test-audio-secret")
    monkeypatch.setenv("GRADIUM_API_KEY", "test-only-key")
    monkeypatch.setenv("GRADIUM_VOICE_ID", "test-voice")
    monkeypatch.setenv("VAPI_TRANSCRIBER_URL_TOKEN", "test-url-token")


def test_caller_channel_forwards_only_caller_pcm() -> None:
    assert gradium_bridge.caller_channel(b"\x01\x00\x09\x00\x02\x00\x08\x00", 2) == b"\x01\x00\x02\x00"
    assert gradium_bridge.caller_channel(b"\x01\x00\x02\x00", 1) == b"\x01\x00\x02\x00"
    with pytest.raises(ValueError, match="whole 16-bit"):
        gradium_bridge.caller_channel(b"\x01\x00\x09", 2)


class FakeTTS:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def send_text(self, text):
        self.text = text

    async def send_eos(self):
        pass

    async def __aiter__(self):
        yield {"type": "audio", "audio": b"\x01\x00\x02\x00"}
        yield {"type": "end_of_stream"}


class FakeSTT:
    def __init__(self):
        self.audio = []
        self.flushes = 0
        self.queue = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def send_audio(self, audio):
        self.audio.append(audio)
        await self.queue.put({"type": "text", "text": "Bonjour"})
        for _ in range(3):
            await self.queue.put({"type": "step", "vad": [{"inactivity_prob": 0.9}]})

    async def send_flush(self, flush_id):
        self.flushes += 1
        await self.queue.put({"type": "flushed"})
        await self.queue.put({"type": "end_of_stream"})

    async def __aiter__(self):
        while True:
            message = await self.queue.get()
            yield message
            if message["type"] == "end_of_stream":
                break


class FakeGradiumClient:
    def __init__(self):
        self.tts = FakeTTS()
        self.stt = FakeSTT()
        self.tts_options = None
        self.stt_options = None

    def tts_realtime(self, **options):
        self.tts_options = options
        return self.tts

    def stt_realtime(self, **options):
        self.stt_options = options
        return self.stt


def test_tts_streams_raw_pcm_at_requested_sample_rate(monkeypatch) -> None:
    _configured(monkeypatch)
    fake = FakeGradiumClient()
    monkeypatch.setattr(gradium_bridge, "_client", lambda: fake)
    response = TestClient(create_app()).post(
        "/v1/voice/gradium/tts",
        json={"message": {"type": "voice-request", "text": "Bonjour", "sampleRate": 24000}},
        headers={"X-Vapi-Secret": "test-audio-secret"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == b"\x01\x00\x02\x00"
    assert fake.tts.text == "Bonjour"
    assert fake.tts_options == {"voice_id": "test-voice", "output_format": "pcm_24000"}


@pytest.mark.parametrize("headers", [
    {"X-Vapi-Secret": "test-audio-secret"},
    {"Authorization": "Bearer test-audio-secret"},
])
def test_stt_forwards_channel_zero_and_emits_partial_then_flush_final(monkeypatch, headers) -> None:
    _configured(monkeypatch)
    fake = FakeGradiumClient()
    monkeypatch.setattr(gradium_bridge, "_client", lambda: fake)
    with TestClient(create_app()).websocket_connect(
        "/v1/voice/gradium/transcriber", headers=headers,
    ) as websocket:
        websocket.send_json({"type": "start", "sampleRate": 16000, "channels": 2})
        websocket.send_bytes(b"\x01\x00\x09\x00\x02\x00\x08\x00")
        partial = websocket.receive_json()
        final = websocket.receive_json()
    assert partial == {"type": "transcriber-response", "transcription": "Bonjour",
                       "channel": "customer", "transcriptType": "partial"}
    assert final == {**partial, "transcriptType": "final"}
    assert fake.stt.audio == [b"\x01\x00\x02\x00"]
    assert fake.stt.flushes == 1
    assert fake.stt_options == {"input_format": "pcm_16000",
                                "json_config": {"language": "fr", "delay_in_frames": 16}}


def test_stt_accepts_scoped_url_token_without_headers(monkeypatch) -> None:
    _configured(monkeypatch)
    fake = FakeGradiumClient()
    monkeypatch.setattr(gradium_bridge, "_client", lambda: fake)
    with TestClient(create_app()).websocket_connect(
        "/v1/voice/gradium/transcriber?token=test-url-token",
    ) as websocket:
        websocket.send_json({"type": "start", "sampleRate": 16000, "channels": 2})
        websocket.send_bytes(b"\x01\x00\x09\x00")
        assert websocket.receive_json()["transcriptType"] == "partial"
        assert websocket.receive_json()["transcriptType"] == "final"
    assert fake.stt.audio == [b"\x01\x00"]


def test_stt_rejects_wrong_url_token(monkeypatch) -> None:
    _configured(monkeypatch)
    with pytest.raises(WebSocketDisconnect) as caught:
        with TestClient(create_app()).websocket_connect(
            "/v1/voice/gradium/transcriber?token=wrong",
        ):
            pass
    assert caught.value.code == 4401


def test_audio_bridge_rejects_missing_credential(monkeypatch) -> None:
    _configured(monkeypatch)
    app = TestClient(create_app())
    response = app.post("/v1/voice/gradium/tts", json={
        "message": {"type": "voice-request", "text": "Bonjour", "sampleRate": 24000},
    })
    assert response.status_code == 401
    with pytest.raises(WebSocketDisconnect) as caught:
        with app.websocket_connect("/v1/voice/gradium/transcriber"):
            pass
    assert caught.value.code == 4401


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 24000, 44100])
@pytest.mark.parametrize("text", [
    "OK, merci beaucoup, on vous envoie un SMS pour vous demander quelques détails supplémentaires et on se charge de votre dossier.",
    "et on se charge de votre dossier.",
])
def test_closing_plays_two_seconds_of_silence_after_speech(monkeypatch, rate, text):
    _configured(monkeypatch)
    fake = FakeGradiumClient()
    monkeypatch.setattr(gradium_bridge, "_client", lambda: fake)
    response = TestClient(create_app()).post(
        "/v1/voice/gradium/tts",
        json={"message": {"type": "voice-request", "text": text, "sampleRate": rate}},
        headers={"X-Vapi-Secret": "test-audio-secret"},
    )
    assert response.status_code == 200
    assert response.content == b"\x01\x00\x02\x00" + b"\x00\x00" * rate * 2
    assert fake.tts.text == text
