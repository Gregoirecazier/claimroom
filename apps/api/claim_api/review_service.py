from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from typing import Any, Protocol
from uuid import UUID

from claim_api.case_service import CaseNotFoundError, StaleCaseError, UnsupportedActorError
from claim_api.review_models import (
    ActionRequest,
    ActionResult,
    ApproveDraftRequest,
    ApprovalView,
    DraftEditRequest,
    DraftView,
    TransmissionPreviewView,
)


class ReviewTransitionError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


class ReviewRepository(Protocol):
    def update_current_draft(
        self, actor_id: UUID, case_id: UUID, request: DraftEditRequest
    ) -> DraftView | None: ...

    def approve_current_draft(
        self, actor_id: UUID, case_id: UUID, request: ApproveDraftRequest
    ) -> ApprovalView | None: ...

    def register_current_draft(
        self, actor_id: UUID, case_id: UUID, request: ActionRequest, idempotency_key: str
    ) -> ActionResult | None: ...

    def send_current_draft(
        self, actor_id: UUID, case_id: UUID, request: ActionRequest, idempotency_key: str
    ) -> ActionResult | None: ...

    def transmission_preview(self, actor_id: UUID, case_id: UUID) -> TransmissionPreviewView | None: ...

    def transmission_receipt(self, actor_id: UUID, case_id: UUID) -> dict[str, Any] | None: ...


def canonical_recipient(value: Any) -> Any:
    """Normalize JSON values before hashing/persisting a recipient payload."""
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReviewTransitionError("invalid_draft", "Recipient values must be finite JSON numbers.")
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value.strip())
    if isinstance(value, list):
        return [canonical_recipient(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {
            unicodedata.normalize("NFC", key.strip()): canonical_recipient(item)
            for key, item in value.items()
        }
    raise ReviewTransitionError("invalid_draft", "Recipient must contain JSON-compatible values only.")


def draft_digest(
    recipient: dict[str, Any] | None,
    amount_minor: int | None,
    currency: str | None,
    body: str,
    attachment_ids: list[UUID] | list[str],
    package: dict[str, Any] | None = None,
    transmission_comment: str = "",
) -> str:
    normalized = {
        "recipient": canonical_recipient(recipient),
        "amount_minor": amount_minor,
        "currency": currency,
        "body": unicodedata.normalize("NFC", body.replace("\r\n", "\n").replace("\r", "\n").strip()),
        "attachment_ids": sorted(str(value) for value in attachment_ids),
    }
    if package is not None:
        normalized["package"] = package
        normalized["transmission_comment"] = unicodedata.normalize("NFC", transmission_comment.strip())
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _actor_uuid(actor_id: str) -> UUID:
    try:
        return UUID(actor_id)
    except ValueError as exc:
        raise UnsupportedActorError("Case storage requires a Supabase user UUID.") from exc


class ReviewActionsService:
    def __init__(self, repository: ReviewRepository) -> None:
        self.repository = repository

    def update_draft(self, actor_id: str, case_id: UUID, request: DraftEditRequest) -> DraftView:
        draft = self.repository.update_current_draft(_actor_uuid(actor_id), case_id, request)
        if draft is None:
            raise CaseNotFoundError
        return draft

    def approve(self, actor_id: str, case_id: UUID, request: ApproveDraftRequest) -> ApprovalView:
        approval = self.repository.approve_current_draft(_actor_uuid(actor_id), case_id, request)
        if approval is None:
            raise CaseNotFoundError
        return approval

    def register(self, actor_id: str, case_id: UUID, request: ActionRequest, idempotency_key: str) -> ActionResult:
        result = self.repository.register_current_draft(_actor_uuid(actor_id), case_id, request, idempotency_key)
        if result is None:
            raise CaseNotFoundError
        return result

    def send(self, actor_id: str, case_id: UUID, request: ActionRequest, idempotency_key: str) -> ActionResult:
        result = self.repository.send_current_draft(_actor_uuid(actor_id), case_id, request, idempotency_key)
        if result is None:
            raise CaseNotFoundError
        return result

    def preview(self, actor_id: str, case_id: UUID) -> TransmissionPreviewView:
        result = self.repository.transmission_preview(_actor_uuid(actor_id), case_id)
        if result is None:
            raise CaseNotFoundError
        return result

    def receipt(self, actor_id: str, case_id: UUID) -> dict[str, Any]:
        result = self.repository.transmission_receipt(_actor_uuid(actor_id), case_id)
        if result is None:
            raise CaseNotFoundError
        return result
