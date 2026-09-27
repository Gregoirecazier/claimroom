"""Recover persisted media workflows even when every browser is closed."""

import asyncio
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def configuration():
    origin = os.getenv("MEDIA_WORKER_API_URL", "").rstrip("/")
    token = os.getenv("MEDIA_WORKER_TOKEN", "")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or len(token) < 32
    ):
        return None
    return origin, token


def tick(config):
    origin, token = config
    request = Request(
        origin + "/internal/media-workflow/tick",
        method="POST",
        data=b"",
        headers={"Authorization": "Bearer " + token},
    )
    with build_opener(NoRedirect()).open(request, timeout=290) as response:
        return bool(json.loads(response.read(2000)).get("processed"))


async def run():
    config = configuration()
    if config is None:
        return
    while True:
        try:
            processed = await asyncio.to_thread(tick, config)
            delay = 1 if processed else 10
        except Exception:
            # No bearer tokens, claim details or remote response bodies in worker logs.
            delay = 30
        await asyncio.sleep(delay)
