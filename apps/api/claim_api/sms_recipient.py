"""Resolve an inbound caller ID as a contact candidate, never as identity proof."""

from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


E164 = re.compile(r"^\+[1-9][0-9]{1,14}$")


class RecipientLookupUnavailable(RuntimeError):
    pass


def validate_e164(number: str) -> str:
    if not E164.fullmatch(number):
        raise ValueError("Recipient must be an E.164 phone number")
    return number


def mask_e164(number: str) -> str:
    validate_e164(number)
    return number[:3] + "•" * max(0, len(number) - 7) + number[-4:]


class VapiCallerLookup:
    def __init__(self, private_key: str, expected_assistant_id: str, open_url=urlopen):
        self.private_key = private_key
        self.expected_assistant_id = expected_assistant_id
        self.open_url = open_url

    @classmethod
    def from_env(cls) -> "VapiCallerLookup":
        key = os.getenv("VAPI_PRIVATE_API_KEY", "")
        assistant_id = os.getenv("VOICE_ASSISTANT_ID", "")
        if not key or not assistant_id:
            raise RecipientLookupUnavailable("Vapi caller lookup is not configured")
        return cls(key, assistant_id)

    def candidate(self, call_id: str) -> str | None:
        if not call_id or len(call_id) > 200 or not all(c.isalnum() or c in "-_" for c in call_id):
            raise ValueError("Invalid call ID")
        request = Request(
            f"https://api.vapi.ai/call/{quote(call_id, safe='')}",
            headers={"Authorization": f"Bearer {self.private_key}", "User-Agent": "Claimroom/1.0"},
        )
        try:
            with self.open_url(request, timeout=8) as response:
                raw = response.read(1_000_001)
        except (HTTPError, URLError, TimeoutError) as error:
            raise RecipientLookupUnavailable("Vapi caller lookup failed") from error
        if len(raw) > 1_000_000:
            raise RecipientLookupUnavailable("Vapi call response is too large")
        try:
            call = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise RecipientLookupUnavailable("Vapi returned an invalid call") from error
        if call.get("id") != call_id or call.get("assistantId") != self.expected_assistant_id:
            raise RecipientLookupUnavailable("Vapi call identity mismatch")
        if call.get("type") not in {"inboundPhoneCall", "outboundPhoneCall"}:
            return None
        customer = call.get("customer") if isinstance(call.get("customer"), dict) else {}
        number = customer.get("number")
        return validate_e164(number) if isinstance(number, str) and E164.fullmatch(number) else None
