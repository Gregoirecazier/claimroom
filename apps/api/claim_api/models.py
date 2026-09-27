from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Intake(ContractModel):
    reported_at: datetime
    insured_reference: str | None = None
    insured_name: str | None = None
    insured_vehicle: str | None = None
    insured_plate: str | None = None
    insured_identity_source: Literal["synthetic_fixture", "handler_entered"] | None = None
    policy_reference: str | None = None
    incident_at: datetime | None = None
    time_source: Literal["caller_statement", "inferred_from_call", "handler_entered", "insured_correction"] | None = None
    location: str | None = None
    vehicle_country: str | None = None
    narrative: str
    danger_status: Literal["yes", "no", "unknown"] | None = None
    injury_status: Literal["yes", "no", "unknown"] | None = None
    missing_fields: list[str] = Field(default_factory=list)

    @field_validator("reported_at", "incident_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value.astimezone(timezone.utc)


class IntakePatch(ContractModel):
    insured_name: str | None = None
    reported_at: datetime | None = None
    insured_reference: str | None = None
    insured_vehicle: str | None = None
    insured_plate: str | None = None
    policy_reference: str | None = None
    incident_at: datetime | None = None
    location: str | None = None
    vehicle_country: str | None = None
    narrative: str | None = None
    danger_status: Literal["yes", "no", "unknown"] | None = None
    injury_status: Literal["yes", "no", "unknown"] | None = None

    @field_validator("reported_at", "incident_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value.astimezone(timezone.utc)


class CaseSummary(ContractModel):
    id: UUID
    created_by_user_id: UUID
    scenario_id: str
    synthetic: Literal[True]
    status: Literal["collecting", "review_ready", "approved", "registered", "sent"]
    state_version: int
    content_revision: int
    created_at: datetime
    updated_at: datetime
    intake: Intake


class ProviderResultView(ContractModel):
    id: UUID
    source_id: UUID
    provider: str
    mode: Literal["mock", "live"]
    status: Literal["matched", "no_match", "ambiguous", "unavailable", "error"]
    source_version: str
    query_hash: str
    query: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime
    data: dict[str, Any]
    reason: str | None = None


class AuditEventView(ContractModel):
    id: UUID
    actor_user_id: UUID | None
    event_type: str
    state_version_before: int
    state_version_after: int
    content_revision_before: int
    content_revision_after: int
    metadata: dict[str, Any]
    occurred_at: datetime


class EvidenceView(ContractModel):
    id: UUID
    case_id: UUID
    kind: str
    storage_path: str
    source_kind: str
    mode: Literal["mock", "live"]
    mime_type: str
    byte_size: int
    client_sha256: str | None
    checksum_status: Literal["client_declared", "verified", "mismatch"]
    sha256_verified: str | None = None
    role: str | None = None
    display_order: int | None = None
    original_filename: str | None = None
    received_at: datetime


class VideoAnalysisLinkView(ContractModel):
    evidence_id: UUID
    artifact_id: UUID
    pipeline_fingerprint: str
    observations: list[dict[str, Any]] = Field(default_factory=list)


class CameraCandidateView(ContractModel):
    id: str
    label: str
    source: str
    controller: str | None
    recipient: str | None
    likely_from: datetime | None
    likely_to: datetime | None
    status: Literal["candidate", "unknown_owner"]
    fixture_event_id: str | None
    mode: Literal["mock"] = "mock"


class CameraSearchView(ContractModel):
    status: Literal["candidates", "unavailable"]
    mode: Literal["mock"] = "mock"
    source_version: str
    reason: str | None = None
    candidates: list[CameraCandidateView] = Field(default_factory=list)


class MappedCameraView(ContractModel):
    id: str
    latitude: float
    longitude: float
    distance_m: int
    label: str
    operator: str | None = None
    camera_type: str | None = None
    source_url: str


class CameraMapView(ContractModel):
    status: Literal["available", "no_cameras", "not_covered", "unresolved", "unavailable"]
    address: str | None = None
    resolved_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius_m: int = 150
    cameras: list[MappedCameraView] = Field(default_factory=list)
    reason: str | None = None


class CameraRequestView(ContractModel):
    id: UUID
    case_id: UUID
    candidate_id: str
    candidate_label: str
    source: str
    controller: str | None
    recipient: str | None
    scope: str
    reason: str
    status: Literal["draft", "requested", "waiting", "denied", "unknown_owner", "received", "unavailable", "timed_out"]
    mode: Literal["mock"]
    fixture_event_id: str | None
    evidence_id: UUID | None
    created_by_user_id: UUID
    approved_by_user_id: UUID | None
    status_actor_user_id: UUID | None
    created_at: datetime
    approved_at: datetime | None
    requested_at: datetime | None
    status_changed_at: datetime | None
    received_at: datetime | None


class CreateCameraRequest(ContractModel):
    candidate_id: str = Field(min_length=1, max_length=100)
    scope: str = Field(min_length=10, max_length=1000)
    reason: str = Field(min_length=10, max_length=1000)
    expected_state_version: int = Field(ge=1)


class CameraRequestTransition(ContractModel):
    expected_state_version: int = Field(ge=1)


class UpdateCameraRequestStatus(CameraRequestTransition):
    status: Literal["waiting", "denied", "unknown_owner", "unavailable", "timed_out"]


class AnalysisSourceRef(ContractModel):
    kind: Literal["intake", "evidence", "provider_result", "estimate", "voice"]
    id: str
    locator: str


class CounterpartyLookupView(ContractModel):
    plate_candidate: str
    country: str
    incident_date: date
    vehicle_track_id: str
    identification_status: Literal["observed", "human_confirmed", "uncertain"]
    supporting_source_refs: list[AnalysisSourceRef]
    validation_reason: str | None = None
    validated_by_user_id: UUID | None = None
    validated_at: datetime | None = None
    insurance_result_id: UUID
    correspondent_result_id: UUID | None = None
    current: bool


class ReportLine(ContractModel):
    id: str
    text: str
    claim_kind: Literal["declaration", "observation", "provider_result", "hypothesis", "handler_edit"]
    source_refs: list[AnalysisSourceRef] = Field(default_factory=list)
    uncertainty: str | None = None
    as_of_revision: int
    stale: bool = False
    mode: Literal["mock", "live"] | None = None
    signed_by: UUID | None = None
    signed_at: datetime | None = None
    previous_text: str | None = None


class ReportView(ContractModel):
    lines: list[ReportLine] = Field(default_factory=list)
    analysis_current: bool = False
    needs_reanalysis: bool = False


class ReportLineEditRequest(ContractModel):
    text: str = Field(min_length=1, max_length=2000)
    uncertainty: str | None = Field(default=None, max_length=500)
    expected_state_version: int = Field(ge=1)

    @field_validator("text")
    @classmethod
    def trim_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Report text cannot be empty.")
        return value


class EstimateItem(ContractModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=200)
    amount_minor: StrictInt = Field(ge=0, le=9_000_000_000_000_000)

    @field_validator("label")
    @classmethod
    def trim_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Estimate item label cannot be empty.")
        return value


class EstimateView(ContractModel):
    id: UUID
    case_id: UUID
    version: int
    line_items: list[EstimateItem]
    total_minor: int
    currency: Literal["EUR"]
    tax_basis: Literal["TTC"]
    estimate_source: Literal["demo_fixture", "handler", "quote", "agent_proposal"]
    source_refs: list[AnalysisSourceRef] = Field(default_factory=list)
    created_by_user_id: UUID | None
    created_at: datetime


class UpsertEstimateRequest(ContractModel):
    line_items: list[EstimateItem] = Field(min_length=1, max_length=50)
    total_minor: StrictInt = Field(gt=0, le=9_000_000_000_000_000)
    currency: Literal["EUR"]
    tax_basis: Literal["TTC"]
    estimate_source: Literal["handler"] = "handler"
    source_refs: list[AnalysisSourceRef] = Field(default_factory=list)
    expected_state_version: int = Field(ge=1)

    @model_validator(mode="after")
    def check_total(self) -> "UpsertEstimateRequest":
        if len({item.id for item in self.line_items}) != len(self.line_items):
            raise ValueError("Estimate item IDs must be unique.")
        if sum(item.amount_minor for item in self.line_items) != self.total_minor:
            raise ValueError("Estimate total must equal the sum of its line items.")
        return self


class QuoteView(ContractModel):
    id: UUID
    case_id: UUID
    version: int
    evidence_id: UUID
    filename: str
    mime_type: Literal["application/pdf"]
    checksum: str
    checksum_status: Literal["client_declared", "verified"]
    total_ttc_minor: int
    amount_source: Literal["handler_entered", "demo_fixture"]
    attached_estimate_version: int | None
    created_by_user_id: UUID
    created_at: datetime


class AttachQuoteRequest(ContractModel):
    evidence_id: UUID
    total_ttc_minor: StrictInt = Field(gt=0, le=9_000_000_000_000_000)
    amount_source: Literal["handler_entered"] = "handler_entered"
    expected_state_version: int = Field(ge=1)


class RemoveQuoteRequest(ContractModel):
    expected_state_version: int = Field(ge=1)


class AnalysisEvidenceInput(ContractModel):
    id: UUID
    kind: str
    source_kind: str
    mode: Literal["mock", "live"]
    mime_type: str
    byte_size: int
    received_at: datetime
    storage_path: str | None = None
    client_sha256: str | None = None


class AnalysisSourceExcerpt(ContractModel):
    source_ref: AnalysisSourceRef
    text: str = Field(max_length=1000)


class AnalysisInputV1(ContractModel):
    schema_version: Literal[1]
    case_id: UUID
    content_revision: int
    scenario_id: str
    intake: Intake
    evidence: list[AnalysisEvidenceInput] = Field(default_factory=list)
    provider_results: list[ProviderResultView] = Field(default_factory=list)
    source_excerpts: list[AnalysisSourceExcerpt] = Field(default_factory=list)


class AnalysisVoiceContext(ContractModel):
    session_id: str
    status: Literal["urgent_human_handoff", "collecting", "complete", "incomplete", "error"]
    facts: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisInputV2(AnalysisInputV1):
    """Immutable, case-local inputs for the current analysis method."""
    schema_version: Literal[2]
    counterparty_lookup: CounterpartyLookupView | None = None
    estimate: EstimateView | None = None
    quote: QuoteView | None = None
    quote_status: Literal["no_estimate", "no_quote", "matched", "mismatch", "outdated"] = "no_estimate"
    voice_session: AnalysisVoiceContext | None = None
    legal_excerpts: list[AnalysisSourceExcerpt] = Field(default_factory=list)


class AnalysisProposition(ContractModel):
    text: str = Field(min_length=1, max_length=1000)
    assessment: Literal["supported", "hypothesis", "contradicted"]
    source_refs: list[AnalysisSourceRef] = Field(default_factory=list)
    uncertainty_note: str | None = Field(default=None, max_length=500)


class AnalysisIssue(ContractModel):
    text: str = Field(min_length=1, max_length=1000)
    source_refs: list[AnalysisSourceRef] = Field(default_factory=list)


class AnalysisRecipient(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    source_refs: list[AnalysisSourceRef] = Field(min_length=1)


class AnalysisAmount(ContractModel):
    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    source_refs: list[AnalysisSourceRef] = Field(min_length=1)


class MediaCitation(ContractModel):
    evidence_id: UUID
    timestamp_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class VisualFinding(ContractModel):
    description: str = Field(min_length=1, max_length=2000)
    confidence: Literal["low", "medium", "high"]
    citations: list[MediaCitation] = Field(min_length=1, max_length=20)


class PlateFinding(VisualFinding):
    vehicle: str
    plate: str | None = None
    legibility: Literal["readable", "partial", "unreadable"]
    role: Literal["insured", "third_party", "unknown"]

    @model_validator(mode="after")
    def check_legibility(self):
        if self.legibility == "readable" and not (self.plate or "").strip():
            raise ValueError("A readable plate requires a transcription")
        if self.legibility == "unreadable" and self.plate is not None:
            raise ValueError("An unreadable plate cannot have a transcription")
        return self


class DamageEstimate(ContractModel):
    minimum_minor: int = Field(ge=0)
    maximum_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    assumptions: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.maximum_minor < self.minimum_minor:
            raise ValueError("Damage estimate range must be ordered")
        return self


class DamageFinding(VisualFinding):
    vehicle: str
    affected_parts: list[str]
    severity: Literal["minor", "moderate", "severe", "unknown"]
    accident_link: Literal["consistent", "uncertain", "inconsistent"]
    estimate: DamageEstimate | None = None


class VehicleLiabilityAssessment(ContractModel):
    vehicle: str = Field(min_length=1, max_length=200)
    role: Literal["insured", "third_party", "unknown"]
    assessment: Literal["likely_responsible", "possibly_contributing", "no_visible_contribution", "undetermined"]
    reasoning: str = Field(min_length=1, max_length=2000)
    confidence: Literal["low", "medium", "high"]
    citations: list[MediaCitation] = Field(min_length=1, max_length=20)


class LiabilityAssessment(ContractModel):
    likely_responsible: Literal["insured", "third_party", "shared", "undetermined"]
    reasoning: str = Field(min_length=1, max_length=3000)
    confidence: Literal["low", "medium", "high"]
    citations: list[MediaCitation] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(min_length=1)
    requires_human_review: Literal[True] = True
    vehicle_assessments: list[VehicleLiabilityAssessment] = Field(default_factory=list, max_length=20)


class MediaAnalysis(ContractModel):
    analyzed_evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=3000)
    observations: list[VisualFinding] = Field(default_factory=list, max_length=30)
    plates: list[PlateFinding] = Field(default_factory=list, max_length=20)
    damages: list[DamageFinding] = Field(default_factory=list, max_length=20)
    liability: LiabilityAssessment
    cross_evidence_consistency: str = Field(min_length=1, max_length=2000)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class AnalysisOutputV1(ContractModel):
    schema_version: Literal[1]
    proposed_route: Literal["subrogation", "handler_review", "insufficient_information"]
    recipient: AnalysisRecipient | None = None
    amount: AnalysisAmount | None = None
    propositions: list[AnalysisProposition] = Field(default_factory=list, max_length=30)
    contradictions: list[AnalysisIssue] = Field(default_factory=list, max_length=20)
    missing_items: list[str] = Field(default_factory=list, max_length=20)
    non_blocking_notes: list[str] = Field(default_factory=list, max_length=20)
    draft_body: str = Field(min_length=1, max_length=5000)
    media_analysis: MediaAnalysis | None = None


class AnalysisGateResult(ContractModel):
    gate: Literal["intake", "counterparty", "evidence", "approval"]
    status: Literal["passed", "needs_review", "blocked"]
    reason_codes: list[str] = Field(default_factory=list)
    source_refs: list[AnalysisSourceRef] = Field(default_factory=list)


class AnalysisRunView(ContractModel):
    id: UUID
    input_content_revision: int
    method_version: str
    status: Literal["running", "ready", "stale", "failed"]
    mode: Literal["mock", "live"]
    output: AnalysisOutputV1 | None = None
    gate_results: list[AnalysisGateResult] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class AnalysisRunRequest(ContractModel):
    expected_state_version: int = Field(ge=1)


class AnalysisRunResponse(ContractModel):
    analysis_run: AnalysisRunView
    case: "CaseView"


class DraftView(ContractModel):
    id: UUID
    case_id: UUID
    analysis_run_id: UUID | None
    parent_draft_id: UUID | None
    version: int
    content_revision: int
    recipient: dict[str, Any] | None = None
    amount_minor: int | None = None
    currency: str | None = None
    body: str
    attachment_ids: list[UUID]
    package: dict[str, Any] | None = None
    transmission_comment: str = ""
    created_by_user_id: UUID | None = None
    sha256: str
    created_at: datetime


class CaseListItem(CaseSummary):
    pass


class CaseListPage(ContractModel):
    items: list[CaseListItem]
    total: int
    offset: int
    limit: int
    has_more: bool


class VoiceCaseView(ContractModel):
    provider: Literal["vapi"]
    session_id: str
    mode: Literal["mock", "live"]
    telephony_provider: Literal["twilio", "web"] = "twilio"
    status: Literal["urgent_human_handoff", "collecting", "complete", "incomplete", "error"]
    missing_p0: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    facts: list[dict[str, Any]] = Field(default_factory=list)
    segments: list[dict[str, Any]] = Field(default_factory=list)
    recording: dict[str, Any] = Field(default_factory=dict)
    call_started_at: datetime


class CaseView(CaseSummary):
    evidence: list[EvidenceView] = Field(default_factory=list)
    camera_requests: list[CameraRequestView] = Field(default_factory=list)
    video_analyses: list[VideoAnalysisLinkView] = Field(default_factory=list)
    provider_results: list[ProviderResultView] = Field(default_factory=list)
    counterparty_lookup: CounterpartyLookupView | None = None
    latest_analysis: AnalysisRunView | None = None
    current_draft: DraftView | None = None
    gate_results: list[AnalysisGateResult] = Field(default_factory=list)
    approval: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[AuditEventView] = Field(default_factory=list)
    report: ReportView = Field(default_factory=ReportView)
    estimate: EstimateView | None = None
    quote: QuoteView | None = None
    quote_status: Literal["no_estimate", "no_quote", "matched", "mismatch", "outdated"] = "no_estimate"
    voice_session: VoiceCaseView | None = None


AnalysisRunResponse.model_rebuild()
AnalysisInputV2.model_rebuild()


class CreateCaseRequest(ContractModel):
    scenario_id: str = Field(min_length=1, max_length=100)


class UpdateIntakeRequest(IntakePatch):
    expected_state_version: int = Field(ge=1)


EvidenceKind = Literal["scene_photo", "vehicle_photo", "damage_photo", "document", "other", "scene_video", "cctv_video", "insured_video"]


class CreateEvidenceUploadIntentRequest(ContractModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: Literal["image/jpeg", "image/png", "image/webp", "application/pdf", "video/mp4", "video/quicktime", "video/webm"]
    byte_size: int = Field(gt=0, le=52_428_800)
    client_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: EvidenceKind
    expected_state_version: int = Field(ge=1)


class EvidenceUploadIntentView(ContractModel):
    bucket: str
    storage_path: str
    token: str
    mime_type: str
    byte_size: int
    expires_at: datetime


class FinalizeEvidenceRequest(ContractModel):
    storage_path: str = Field(min_length=1, max_length=512)
    client_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: EvidenceKind
    expected_state_version: int = Field(ge=1)


class EvidenceReadUrlView(ContractModel):
    evidence_id: UUID
    url: str
    expires_at: datetime


class ReceiveCCTVRequest(ContractModel):
    fixture_event_id: str = Field(min_length=1, max_length=100)
    expected_state_version: int = Field(ge=1)
    camera_request_id: UUID | None = None


class SeedG1MediaRequest(ContractModel):
    expected_state_version: int = Field(ge=1)


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    email: str | None = None


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ApiError
