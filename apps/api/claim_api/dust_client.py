"""Small non-blocking Dust Conversations client. Never retries conversation creation."""

from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from claim_api.dust_models import DustRole

DEFAULT_AGENTS = {
    "legal": "sduIybtm6M",
    "cctv": "qBEkK3Nkg3",
    "repair": "HwU56jOUcl",
    "garage": "39cz24sZfG",
    "recovery": "QC7kvgzlNa",
}


class DustError(RuntimeError):
    def __init__(self, code: str, *, uncertain: bool = False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the bearer secret to a redirected host.


class DustClient:
    def __init__(
        self,
        *,
        api_key: str,
        workspace_id: str,
        base_url: str,
        agents: dict[str, str],
        open_url=None,
    ):
        self.api_key = api_key
        self.workspace_id = workspace_id
        self.base_url = base_url.rstrip("/")
        self.agents = agents
        self.open_url = open_url or build_opener(NoRedirects()).open

    @classmethod
    def from_env(cls):
        return cls(
            api_key=os.getenv("DUST_API_KEY", "").strip(),
            workspace_id=os.getenv("DUST_WORKSPACE_ID", "0G0kj9rhj8").strip(),
            base_url=os.getenv("DUST_API_BASE_URL", "https://dust.tt").strip(),
            agents={
                role: os.getenv(f"DUST_AGENT_{role.upper()}", value).strip()
                for role, value in DEFAULT_AGENTS.items()
            },
        )

    def check_configuration(self, role: DustRole):
        if (
            not self.api_key
            or self.base_url not in {"https://dust.tt", "https://eu.dust.tt"}
            or not re.fullmatch(r"[a-zA-Z0-9_-]+", self.workspace_id)
            or not re.fullmatch(r"[a-zA-Z0-9_-]+", self.agents.get(role, ""))
        ):
            raise DustError("dust_not_configured")

    def _request(self, method: str, path: str, body=None):
        request = Request(
            self.base_url
            + f"/api/v1/w/{self.workspace_id}/assistant/conversations"
            + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.open_url(request, timeout=25) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("oversize")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError("invalid response")
                return result
        except HTTPError as error:
            code = {
                401: "dust_unauthorized",
                403: "dust_forbidden",
                429: "dust_rate_limited",
            }.get(error.code, "dust_request_failed")
            raise DustError(
                code, uncertain=method == "POST" and error.code >= 500
            ) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise DustError("dust_unavailable", uncertain=method == "POST") from None

    def create(self, role: DustRole, title: str, content: str) -> str:
        self.check_configuration(role)
        response = self._request(
            "POST",
            "",
            {
                "title": title,
                "blocking": False,
                "skipToolsValidation": False,
                "message": {
                    "content": content,
                    "mentions": [{"configurationId": self.agents[role]}],
                    "context": {"username": "claimroom", "timezone": "Europe/Paris"},
                },
            },
        )
        conversation = response.get("conversation")
        cid = conversation.get("sId") if isinstance(conversation, dict) else None
        if not isinstance(cid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", cid):
            raise DustError("dust_invalid_response", uncertain=True)
        return cid

    def read(self, conversation_id: str, agent_id: str) -> tuple[str, str | None]:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", conversation_id):
            raise DustError("dust_invalid_response")
        response = self._request("GET", f"/{conversation_id}")
        try:
            conversation = response["conversation"]
            if conversation["sId"] != conversation_id:
                raise ValueError("mismatched conversation")
            messages = [
                m
                for group in conversation["content"]
                for m in group
                if m.get("type") == "agent_message"
                and m.get("configuration", {}).get("sId") == agent_id
            ]
            if not messages:
                return "running", None
            message = messages[-1]
            status = message.get("status")
            if status in {"succeeded", "gracefully_stopped"}:
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("missing answer")
                return "ready", content
            if status in {"failed", "cancelled", "interrupted"}:
                return "failed", None
            if message.get("creditSpendCheckpointStatus") == "paused" or any(
                action.get("status", "").startswith("blocked_")
                for action in message.get("actions", [])
            ):
                return "needs_action", None
            if status == "created":
                return "running", None
            raise ValueError("unknown status")
        except (KeyError, TypeError, AttributeError, ValueError):
            raise DustError("dust_invalid_response") from None
