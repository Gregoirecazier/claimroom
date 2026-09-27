import importlib.util
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "media_worker", Path(__file__).parents[2] / "voice-bridge" / "media_worker.py"
)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


@pytest.mark.parametrize(
    "origin",
    [
        "http://api.example.com",
        "https://user:secret@api.example.com",
        "https://api.example.com/path",
        "https://api.example.com?token=x",
        "",
    ],
)
def test_worker_rejects_origins_that_could_misroute_its_secret(monkeypatch, origin):
    monkeypatch.setenv("MEDIA_WORKER_API_URL", origin)
    monkeypatch.setenv("MEDIA_WORKER_TOKEN", "x" * 40)
    assert worker.configuration() is None


def test_worker_calls_only_internal_tick_and_does_not_follow_redirects(monkeypatch):
    monkeypatch.setenv("MEDIA_WORKER_API_URL", "https://api.example.com/")
    monkeypatch.setenv("MEDIA_WORKER_TOKEN", "x" * 40)
    calls = []

    def open_url(request, timeout):
        calls.append(request)
        return BytesIO(b'{"processed":true}')

    monkeypatch.setattr(
        worker, "build_opener", lambda handler: SimpleNamespace(open=open_url)
    )
    assert worker.tick(worker.configuration()) is True
    assert calls[0].full_url == "https://api.example.com/internal/media-workflow/tick"
    assert calls[0].get_header("Authorization") == "Bearer " + "x" * 40
    assert (
        worker.NoRedirect().redirect_request(None, None, None, None, None, None) is None
    )


def test_worker_requires_a_sufficient_server_secret(monkeypatch):
    monkeypatch.setenv("MEDIA_WORKER_API_URL", "https://api.example.com")
    monkeypatch.setenv("MEDIA_WORKER_TOKEN", "short")
    assert worker.configuration() is None
