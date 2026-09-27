from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from claim_api.review_models import ActionReceiptView, ApprovalView, DraftView


class ClaimRegistry(Protocol):
    """Port for registering a reviewed claim; the hackathon adapter stays local."""

    def register(self, draft: DraftView, approval: ApprovalView) -> str: ...


class ClaimSender(Protocol):
    """Port for sending the reviewed claim; the hackathon adapter stays local."""

    def send(self, draft: DraftView, approval: ApprovalView, registration: ActionReceiptView) -> str: ...


class SimulatedClaimRegistry:
    def register(self, draft: DraftView, approval: ApprovalView) -> str:
        del draft, approval
        return f"SIM-CLAIM-{uuid4().hex[:12].upper()}"


class SimulatedClaimSender:
    def send(self, draft: DraftView, approval: ApprovalView, registration: ActionReceiptView) -> str:
        del draft, approval
        if registration.status != "confirmed" or registration.kind != "registration":
            raise ValueError("A confirmed simulated registration is required before sending.")
        return f"SIM-MSG-{uuid4().hex[:12].upper()}"
