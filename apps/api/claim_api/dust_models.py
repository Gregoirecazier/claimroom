"""Review-only contracts for the five Dust specialists, separate from media analysis."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from claim_api.models import ContractModel, DamageEstimate, MediaCitation

DustRole = Literal["legal", "cctv", "repair", "garage", "recovery"]
DustStatus = Literal[
    "submitting",
    "running",
    "needs_action",
    "ready",
    "failed",
    "submission_unknown",
    "stale",
]


class DustDocument(ContractModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=12000)


class DustRunRequest(ContractModel):
    agent: DustRole
    expected_state_version: int = Field(ge=1)
    idempotency_key: UUID
    documents: list[DustDocument] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def unique_documents(self):
        if len({item.id for item in self.documents}) != len(self.documents):
            raise ValueError("Document IDs must be unique")
        return self


class DustSource(ContractModel):
    id: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=300)
    # Public URLs are evidence for review, not automatically trusted facts.
    url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://")
    input_reference: str | None = Field(default=None, max_length=200)
    media_citation: MediaCitation | None = None

    @model_validator(mode="after")
    def identifiable(self):
        if (
            sum(
                x is not None
                for x in (self.url, self.input_reference, self.media_citation)
            )
            != 1
        ):
            raise ValueError(
                "A source must reference exactly one URL, input, or Gemini citation"
            )
        return self


class DustFinding(ContractModel):
    text: str = Field(min_length=1, max_length=2000)
    assessment: Literal["supported", "hypothesis", "contradicted"]
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class DustReport(ContractModel):
    case_id: UUID
    content_revision: int = Field(ge=1)
    agent: DustRole
    mode: Literal["demo"]
    status: Literal["review_required", "needs_information"]
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[DustFinding] = Field(default_factory=list, max_length=40)
    sources: list[DustSource] = Field(default_factory=list, max_length=60)
    missing_information: list[str] = Field(default_factory=list, max_length=30)
    proposed_actions: list[str] = Field(default_factory=list, max_length=30)
    draft_body: str | None = Field(default=None, max_length=12000)
    repair_estimate: DamageEstimate | None = None
    human_review_required: Literal[True]


class DustRunView(ContractModel):
    id: UUID
    case_id: UUID
    agent: DustRole
    agent_id: str
    input_content_revision: int
    status: DustStatus
    conversation_id: str | None = None
    gemini_analysis_run_id: UUID | None = None
    output: DustReport | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime
