"""Public S09 contract. Message history never returns a reusable deposit token."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SmsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecipientConfirmation(SmsModel):
    kind: Literal["caller_id", "manager_correction"]
    number: str | None = None
    reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def valid_confirmation(self) -> "RecipientConfirmation":
        if self.kind == "caller_id" and self.number is not None:
            raise ValueError("Caller ID is resolved by the server")
        if self.kind == "manager_correction" and (not self.number or not (self.reason or "").strip()):
            raise ValueError("A corrected recipient needs a number and reason")
        return self


class SmsPreviewRequest(SmsModel):
    expected_state_version: int = Field(ge=0)
    recipient_confirmation: RecipientConfirmation


class SmsPreview(SmsModel):
    recipient_masked: str
    body: str
    deposit_url: str
    missing_items: list[str]
    source_refs: list[str]
    deposit_grant_id: UUID
    deposit_link_expires_at: datetime
    content_revision: int
    preview_hash: str
    warnings: list[str]


class SmsCreateRequest(SmsModel):
    expected_state_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    recipient_confirmation: RecipientConfirmation
    deposit_grant_id: UUID
    deposit_url: str = Field(min_length=20, max_length=2048)
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SmsMessageView(SmsModel):
    id: UUID
    case_id: UUID
    recipient_masked: str
    body_preview: str
    mode: Literal["mock", "live"]
    provider: str
    status: Literal["queued", "accepted", "sent", "delivered", "failed", "unknown"]
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None


class SmsCreateResponse(SmsModel):
    message: SmsMessageView
    replayed: bool
    state_version: int


class SmsLinkResponse(SmsModel):
    url: str
    expires_at: datetime


class SmsMockTransitionRequest(SmsModel):
    expected_state_version: int = Field(ge=0)
    status: Literal["accepted", "sent", "delivered", "failed", "unknown"]
    error_code: str | None = Field(default=None, max_length=100)


class SmsMockTransitionResponse(SmsModel):
    message: SmsMessageView
    state_version: int
    replayed: bool


class WhatsAppInboundView(SmsModel):
    id: UUID
    body: str
    media_count: int
    evidence_id: UUID | None
    received_at: datetime
