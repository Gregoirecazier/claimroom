from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from claim_api.models import ContractModel


class DraftView(ContractModel):
    id: UUID
    case_id: UUID
    analysis_run_id: UUID | None
    parent_draft_id: UUID | None
    version: int
    content_revision: int
    recipient: dict[str, Any] | None
    amount_minor: int | None
    currency: str | None
    body: str
    attachment_ids: list[UUID]
    package: dict[str, Any] | None = None
    transmission_comment: str = ""
    created_by_user_id: UUID | None = None
    sha256: str
    created_at: datetime


class ApprovalView(ContractModel):
    id: UUID
    case_id: UUID
    draft_id: UUID
    actor_user_id: UUID
    draft_sha256: str
    approved_content_revision: int
    approved_at: datetime
    superseded_at: datetime | None = None


class ActionReceiptView(ContractModel):
    id: UUID
    case_id: UUID
    kind: Literal["registration", "send"]
    mode: Literal["mock"]
    status: Literal["confirmed", "unknown", "failed"]
    approval_id: UUID
    draft_id: UUID
    idempotency_key: str
    reference: str | None
    created_at: datetime
    registration_action_id: UUID | None = None
    envelope: dict[str, Any] | None = None


class DraftEditRequest(ContractModel):
    recipient: dict[str, Any] | None = None
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    body: str | None = Field(default=None, max_length=20_000)
    attachment_ids: list[UUID] | None = None
    transmission_comment: str | None = Field(default=None, max_length=4000)
    expected_state_version: int = Field(ge=1)

    @field_validator("body")
    @classmethod
    def trim_body(cls, value: str | None) -> str | None:
        return None if value is None else value.replace("\r\n", "\n").replace("\r", "\n").strip()

    @model_validator(mode="after")
    def require_editable_field(self) -> "DraftEditRequest":
        if not (set(self.model_fields_set) - {"expected_state_version"}):
            raise ValueError("At least one draft field must be changed.")
        return self


class ApproveDraftRequest(ContractModel):
    draft_id: UUID
    draft_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    confirmed_review: bool
    expected_state_version: int = Field(ge=1)


class ActionRequest(ContractModel):
    expected_state_version: int = Field(ge=1)


class ActionResult(ContractModel):
    receipt: ActionReceiptView
    created: bool


class TransmissionPreviewView(ContractModel):
    schema_version: int = 1
    mode: Literal["mock"] = "mock"
    simulation: str = "SIMULATION — no external transmission"
    case_id: UUID
    draft_id: UUID
    draft_sha256: str
    content_revision: int
    approval_id: UUID | None = None
    registration_action_id: UUID | None = None
    registration_reference: str | None = None
    recipient: dict[str, Any] | None
    body: str
    transmission_comment: str
    amount_minor: int | None
    currency: str | None
    package: dict[str, Any]
    attachments: list[dict[str, Any]]
