"""Case-scoped chat history, separate from claim facts and outbound messages."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from claim_api.insured_portal import CORRECTABLE


class HistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HistoryMessage(HistoryModel):
    id: UUID
    role: Literal["insured", "assistant"]
    text: str = Field(min_length=1, max_length=4000)
    created_at: datetime


class HistoryProposal(HistoryModel):
    field: str
    value: str = Field(min_length=1, max_length=1000)

    @field_validator("field")
    @classmethod
    def correctable_field(cls, value):
        if value not in CORRECTABLE:
            raise ValueError("Unsupported correction")
        return value


class HistoryState(HistoryModel):
    phase: Literal["confirm", "correct", "collect", "photos", "complete"]
    opening: str = Field(min_length=1, max_length=12000)
    openingAt: datetime
    messages: list[HistoryMessage] = Field(default_factory=list, max_length=1000)
    proposal: HistoryProposal | None = None

    @model_validator(mode="after")
    def unique_messages(self):
        if len({m.id for m in self.messages}) != len(self.messages):
            raise ValueError("Duplicate message IDs")
        return self


class SaveHistoryRequest(HistoryModel):
    expected_revision: int = Field(ge=0)
    state: HistoryState
