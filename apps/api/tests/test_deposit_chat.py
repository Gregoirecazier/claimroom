from __future__ import annotations

import pytest

from claim_api import deposit_chat
from claim_api.deposit_grants import DepositGrantError


class Portal:
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.summary_calls = 0

    def authorize(self, token, capability):
        assert token == "guest-session" and capability == "correct_intake"
        if not self.authorized:
            raise DepositGrantError("invalid_session")

    def summary(self, token):
        self.summary_calls += 1
        return {"intake": {"location": None, "narrative": "Accrochage"},
                "missing_fields": ["location"]}


def test_chat_proposes_only_a_confirmable_correction(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def fake_execute(context_json):
        assert '"location": null' in context_json
        assert '"text": "L’accident a eu lieu place de l’Étoile"' in context_json
        return {"reply": "J’ai noté le lieu. Confirmez-vous cette correction ?",
                "proposal_field": "location", "proposal_value": "place de l’Étoile"}

    monkeypatch.setattr(deposit_chat, "_execute", fake_execute)
    portal = Portal()
    result = deposit_chat.reply(portal, "guest-session", deposit_chat.ChatRequest(
        message="L’accident a eu lieu place de l’Étoile"))
    assert result.proposal == deposit_chat.ChatProposal(field="location", value="place de l’Étoile")
    assert portal.summary_calls == 1
    assert not hasattr(portal, "correct")  # Chat never writes the claim.


def test_invalid_model_proposal_is_not_exposed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def fake_execute(_):
        return {"reply": "Pouvez-vous préciser ?", "proposal_field": "coverage_approved",
                "proposal_value": "yes"}

    monkeypatch.setattr(deposit_chat, "_execute", fake_execute)
    result = deposit_chat.reply(Portal(), "guest-session", deposit_chat.ChatRequest(message="Oui"))
    assert result.proposal is None
    assert deposit_chat._proposal("incident_at", "2026-09-26T15:00:00") is None
    assert deposit_chat._proposal("injury_status", "grave") is None


def test_chat_authorizes_before_model_call(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def unexpected(_):
        pytest.fail("model must not run")

    monkeypatch.setattr(deposit_chat, "_execute", unexpected)
    with pytest.raises(DepositGrantError, match="invalid_session"):
        deposit_chat.reply(Portal(authorized=False), "guest-session",
                           deposit_chat.ChatRequest(message="Bonjour"))


def test_guided_chat_rejects_a_proposal_for_another_field(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def fake_execute(context_json):
        assert '"target_field": "location"' in context_json
        return {"reply": "Confirmez-vous votre référence ?",
                "proposal_field": "insured_reference", "proposal_value": "REF-43"}

    monkeypatch.setattr(deposit_chat, "_execute", fake_execute)
    result = deposit_chat.reply(Portal(), "guest-session", deposit_chat.ChatRequest(
        message="Je suis à Paris", target_field="location"))
    assert result.proposal is None


def test_guided_analysis_answer_keeps_both_target_and_analysis_requests(monkeypatch):
    import json
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    question = "Votre véhicule était-il arrêté avant le choc ?"

    class AnalysisPortal(Portal):
        def summary(self, token):
            return {**super().summary(token), "analysis_requests": [question]}

    async def fake_execute(context_json):
        context = json.loads(context_json)
        assert context["target_field"] == "narrative"
        assert context["case"]["analysis_requests"] == [question]
        assert context["case"]["intake"]["narrative"] == "Accrochage"
        return {"reply": "Confirmez-vous ce complément ?", "proposal_field": "narrative",
                "proposal_value": "Accrochage. Mon véhicule était déjà arrêté."}

    monkeypatch.setattr(deposit_chat, "_execute", fake_execute)
    result = deposit_chat.reply(AnalysisPortal(), "guest-session", deposit_chat.ChatRequest(
        message="Mon véhicule était déjà arrêté.", target_field="narrative"))
    assert result.proposal == deposit_chat.ChatProposal(
        field="narrative", value="Accrochage. Mon véhicule était déjà arrêté.")


def test_chat_proposes_full_name_in_the_name_field(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def fake_execute(context_json):
        assert '"target_field": "insured_name"' in context_json
        return {"reply": "Confirmez-vous votre nom ?", "proposal_field": "insured_name",
                "proposal_value": "Alex Martin"}

    monkeypatch.setattr(deposit_chat, "_execute", fake_execute)
    result = deposit_chat.reply(Portal(), "guest-session", deposit_chat.ChatRequest(
        message="Alex Martin", target_field="insured_name"))
    assert result.proposal == deposit_chat.ChatProposal(field="insured_name", value="Alex Martin")
