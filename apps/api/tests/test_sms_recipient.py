from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from claim_api.sms_recipient import RecipientLookupUnavailable, VapiCallerLookup, mask_e164, validate_e164


def test_caller_id_is_candidate_only_for_expected_inbound_call() -> None:
    seen = []

    def open_url(request, timeout):
        seen.append((request.full_url, request.get_header("Authorization"), request.get_header("User-agent")))
        return BytesIO(json.dumps({"id": "call-1", "assistantId": "assistant-1",
                                   "type": "inboundPhoneCall", "customer": {"number": "+33100000000"}}).encode())

    assert VapiCallerLookup("secret", "assistant-1", open_url).candidate("call-1") == "+33100000000"
    assert seen == [("https://api.vapi.ai/call/call-1", "Bearer secret", "Claimroom/1.0")]
    assert mask_e164("+33100000000").endswith("0000")


def test_outbound_call_uses_customer_number_not_twilio_number() -> None:
    def open_url(request, timeout):
        return BytesIO(json.dumps({"id": "call-2", "assistantId": "assistant-1",
                                   "type": "outboundPhoneCall",
                                   "customer": {"number": "+33612345678"},
                                   "phoneNumber": {"number": "+33100000000"}}).encode())

    assert VapiCallerLookup("secret", "assistant-1", open_url).candidate("call-2") == "+33612345678"


def test_caller_lookup_rejects_wrong_assistant_and_invalid_number() -> None:
    def wrong_assistant(request, timeout):
        return BytesIO(json.dumps({"id": "call-1", "assistantId": "other",
                                   "type": "inboundPhoneCall", "customer": {"number": "+33100000000"}}).encode())

    with pytest.raises(RecipientLookupUnavailable, match="identity mismatch"):
        VapiCallerLookup("secret", "assistant-1", wrong_assistant).candidate("call-1")
    with pytest.raises(ValueError, match="E.164"):
        validate_e164("0100000000")


def test_caller_lookup_normalizes_provider_failure() -> None:
    def blocked(request, timeout):
        raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

    with pytest.raises(RecipientLookupUnavailable, match="lookup failed"):
        VapiCallerLookup("secret", "assistant-1", blocked).candidate("call-1")
