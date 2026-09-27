"""Twilio SMS transport and signed webhook verification, without send retries."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from claim_api.garages import NoRedirects


class SmsError(RuntimeError):
    def __init__(self, code: str, *, uncertain: bool = False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


class TwilioSms:
    def __init__(self, *, open_url=None):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        self.from_number = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
        self.messaging_service_sid = (os.getenv("TWILIO_MESSAGING_SERVICE_SID") or os.getenv("TWILIO_SMS_MESSAGING_SERVICE_SID", "")).strip()
        self.public_url = os.getenv("TWILIO_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if not self.public_url:
            existing = os.getenv("TWILIO_SMS_WEBHOOK_BASE_URL", "").strip().rstrip("/")
            parsed = urlsplit(existing)
            # Existing invitation callbacks use a path, whereas garage
            # callbacks live alongside them at the same API origin.
            self.public_url = existing.removesuffix("/v1/webhooks/twilio/sms") if parsed.path == "/v1/webhooks/twilio/sms" else existing
        self.open_url = open_url or build_opener(NoRedirects()).open

    def check_configuration(self):
        parsed = urlsplit(self.public_url)
        if (
            not re.fullmatch(r"AC[0-9a-fA-F]{32}", self.account_sid)
            or not self.auth_token
            or parsed.scheme != "https" or not parsed.hostname
            or parsed.query or parsed.fragment or parsed.username or parsed.path
            or (not self.messaging_service_sid and not re.fullmatch(r"\+[1-9][0-9]{1,14}", self.from_number))
            or (self.from_number and not re.fullmatch(r"\+[1-9][0-9]{1,14}", self.from_number))
            or (self.messaging_service_sid and not re.fullmatch(r"MG[0-9a-fA-F]{32}", self.messaging_service_sid))
        ):
            raise SmsError("twilio_not_configured")

    def validate_signature(self, path: str, fields: dict[str, list[str]], signature: str) -> bool:
        self.check_configuration()
        value = self.public_url + path
        for key in sorted(fields):
            for item in sorted(set(fields[key])):
                value += key + item
        expected = base64.b64encode(hmac.new(self.auth_token.encode(), value.encode(), hashlib.sha1).digest()).decode()
        return hmac.compare_digest(expected, signature)

    def send(self, recipient: str, body: str, message_id: str) -> str:
        self.check_configuration()
        fields = {
            "To": recipient, "Body": body,
            "StatusCallback": f"{self.public_url}/v1/webhooks/twilio/status/{message_id}",
        }
        if self.messaging_service_sid:
            fields["MessagingServiceSid"] = self.messaging_service_sid
        else:
            fields["From"] = self.from_number
        authorization = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        request = Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json",
            data=urlencode(fields).encode(),
            headers={"Authorization": "Basic " + authorization, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self.open_url(request, timeout=15) as response:
                raw = response.read(100_001)
                if len(raw) > 100_000:
                    raise ValueError("oversize response")
                payload = json.loads(raw)
                sid = payload.get("sid") if isinstance(payload, dict) else None
                if not isinstance(sid, str) or not re.fullmatch(r"SM[0-9a-fA-F]{32}", sid):
                    raise ValueError("invalid SID")
                return sid
        except HTTPError as exc:
            raise SmsError(f"twilio_http_{exc.code}", uncertain=exc.code >= 500) from None
        except (URLError, TimeoutError, OSError, ValueError):
            # A timeout may occur after Twilio has accepted the message.
            raise SmsError("twilio_submission_unknown", uncertain=True) from None


def delivery_status(current: str, incoming: str) -> str:
    mapped = {"accepted": "accepted", "queued": "accepted", "sending": "accepted", "sent": "sent",
              "delivered": "delivered", "undelivered": "failed", "failed": "failed", "canceled": "failed"}.get(incoming)
    if mapped is None or current == "delivered":
        return current
    if mapped == "delivered":
        return mapped
    if current == "failed":
        return current
    ranks = {"queued": 0, "unknown": 0, "accepted": 1, "sent": 2, "failed": 3}
    return mapped if ranks[mapped] >= ranks.get(current, 0) else current
