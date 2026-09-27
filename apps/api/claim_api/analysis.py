from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from claim_api.models import (
    AnalysisAmount,
    AnalysisEvidenceInput,
    AnalysisGateResult,
    AnalysisInputV1,
    AnalysisInputV2,
    AnalysisIssue,
    AnalysisOutputV1,
    AnalysisProposition,
    AnalysisRecipient,
    AnalysisRunRequest,
    AnalysisRunResponse,
    AnalysisRunView,
    AnalysisSourceExcerpt,
    AnalysisSourceRef,
    AnalysisVoiceContext,
    CaseView,
    Intake,
)
from claim_api.video_reuse import configured_pipeline, configured_vision_pipeline
from claim_api.mock_insurance import SOURCE_VERSION as MOCK_INSURANCE_SOURCE_VERSION


METHOD_VERSION = "claims-analysis-v2.1.0"
EVIDENCE_LOCATORS = ("kind", "source_kind", "mime_type", "byte_size", "received_at")
_PIPELEX_BOOT_LOCK = threading.Lock()
_PIPELEX_BOOTED = False


class AnalysisError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AnalysisInProgressError(RuntimeError):
    pass


class UrgentHandoffError(RuntimeError):
    pass


class AnalysisRepository(Protocol):
    def get_case(self, actor_id: UUID, case_id: UUID) -> CaseView | None: ...
    def begin_analysis(self, actor_id: UUID, case_id: UUID, expected_state_version: int,
                       run_id: UUID, method_version: str, mode: str, input_json: dict[str, Any]) -> int: ...
    def complete_analysis(self, actor_id: UUID, case_id: UUID, run_id: UUID,
                          input_content_revision: int, *, status: str, mode: str,
                          output: AnalysisOutputV1 | None, gates: list[AnalysisGateResult],
                          error_code: str | None, error_message: str | None,
                          draft: dict[str, Any] | None) -> CaseView | None: ...


class ClaimsAnalyzer(Protocol):
    mode: Literal["mock", "live"]
    method_version: str
    def analyze(self, analysis_input: AnalysisInputV1) -> AnalysisOutputV1: ...


def build_analysis_input(case: CaseView) -> AnalysisInputV2:
    from claim_api.counterparty_lookup import current_result_ids
    selected_ids = current_result_ids(case)
    provider_results = [item for item in case.provider_results if item.provider not in {"insurance_lookup", "correspondent_lookup"}
                        or (selected_ids is None or item.id in selected_ids)]
    excerpts: list[AnalysisSourceExcerpt] = []
    for field, value in case.intake.model_dump(mode="json").items():
        if field == "missing_fields" or value is None or value == "":
            continue
        excerpts.append(AnalysisSourceExcerpt(
            source_ref=AnalysisSourceRef(kind="intake", id=str(case.id), locator=field),
            text=f"{field}: {value}"[:1000],
        ))
    for item in case.evidence:
        for field in EVIDENCE_LOCATORS:
            value = getattr(item, field)
            excerpts.append(AnalysisSourceExcerpt(
                source_ref=AnalysisSourceRef(kind="evidence", id=str(item.id), locator=field),
                text=f"evidence {field}: {value}"[:1000],
            ))
    for linked in case.video_analyses:
        if linked.pipeline_fingerprint != configured_pipeline().fingerprint:
            continue
        for observation in linked.observations:
            start = observation.get("start_ms")
            end = observation.get("end_ms")
            description = observation.get("description")
            if not isinstance(start, int) or not isinstance(end, int) or not isinstance(description, str):
                continue
            locator = f"video:{start}-{end}"
            category = observation.get("category", "other")
            status = observation.get("status", "observed")
            uncertainty = observation.get("uncertainty")
            excerpts.append(AnalysisSourceExcerpt(
                source_ref=AnalysisSourceRef(kind="evidence", id=str(linked.evidence_id), locator=locator),
                text=(f"video {start}-{end}ms [{category}; {status}]: {description}"
                      + (f"; uncertainty: {uncertainty}" if uncertainty else ""))[:1000],
            ))
    for item in provider_results:
        if item.provider == "vision":
            active_fingerprint = configured_vision_pipeline().fingerprint
            if item.status == "matched" and item.data.get("pipeline_fingerprint") == active_fingerprint:
                for raw in item.data.get("observations", []):
                    if not isinstance(raw, dict) or raw.get("media_id") not in {str(e.id) for e in case.evidence}:
                        continue
                    try:
                        ref = AnalysisSourceRef.model_validate(raw["source_ref"])
                        if ref.kind != "evidence" or ref.id != raw["media_id"]:
                            continue
                        excerpts.append(AnalysisSourceExcerpt(source_ref=ref,
                            text=(f"visual observation [{raw.get('category', 'other')}; {raw['status']}]: {raw['text']}")[:1000]))
                    except (KeyError, ValueError):
                        continue
            continue
        for field, value in item.query.items():
            excerpts.append(AnalysisSourceExcerpt(
                source_ref=AnalysisSourceRef(kind="provider_result", id=str(item.id), locator=f"query.{field}"),
                text=f"{item.provider} query {field}: {value}"[:1000],
            ))
        for field, value in item.data.items():
            excerpts.append(AnalysisSourceExcerpt(
                source_ref=AnalysisSourceRef(kind="provider_result", id=str(item.id), locator=f"data.{field}"),
                text=f"{item.provider} result {field}: {value}"[:1000],
            ))
        if item.reason:
            excerpts.append(AnalysisSourceExcerpt(
                source_ref=AnalysisSourceRef(kind="provider_result", id=str(item.id), locator="reason"),
                text=f"{item.provider} result reason: {item.reason}"[:1000],
            ))
    if case.estimate:
        for field in ("total_minor", "currency", "estimate_source", "version"):
            excerpts.append(AnalysisSourceExcerpt(
                source_ref=AnalysisSourceRef(kind="estimate", id=str(case.estimate.id), locator=field),
                text=f"estimate {field}: {getattr(case.estimate, field)}",
            ))
        for item in case.estimate.line_items:
            excerpts.append(AnalysisSourceExcerpt(
                source_ref=AnalysisSourceRef(kind="estimate", id=str(case.estimate.id), locator=f"line_items.{item.id}"),
                text=f"estimate line {item.id}: {item.label} ({item.amount_minor} minor units)"[:1000],
            ))
    if case.voice_session:
        for index, fact in enumerate(case.voice_session.facts):
            excerpts.append(AnalysisSourceExcerpt(
                source_ref=AnalysisSourceRef(kind="voice", id=case.voice_session.session_id, locator=f"facts.{index}"),
                text=f"voice fact: {json.dumps(fact, ensure_ascii=False, default=str)}"[:1000],
            ))
    return AnalysisInputV2(
        schema_version=2,
        case_id=case.id,
        content_revision=case.content_revision,
        scenario_id=case.scenario_id,
        intake=case.intake,
        evidence=[AnalysisEvidenceInput(
            id=item.id, kind=item.kind, source_kind=item.source_kind, mode=item.mode,
            mime_type=item.mime_type, byte_size=item.byte_size, received_at=item.received_at,
            storage_path=item.storage_path, client_sha256=item.client_sha256,
        ) for item in case.evidence],
        provider_results=provider_results,
        source_excerpts=excerpts,
        counterparty_lookup=case.counterparty_lookup,
        estimate=case.estimate,
        quote=case.quote,
        quote_status=case.quote_status,
        voice_session=AnalysisVoiceContext(
            session_id=case.voice_session.session_id, status=case.voice_session.status,
            facts=case.voice_session.facts,
        ) if case.voice_session else None,
    )


def correspondent_query_matches(insurance: Any, correspondent: Any) -> bool:
    """Require the directory lookup to use the exact matched insurer/jurisdictions."""
    if insurance and insurance.source_version == MOCK_INSURANCE_SOURCE_VERSION:
        return bool(
            insurance.status == "matched" and correspondent and correspondent.status == "matched"
            and insurance.data.get("insurer_id")
            and correspondent.query.get("insurer_id") == insurance.data.get("insurer_id")
            and correspondent.query.get("accident_country") == "FR"
            and correspondent.query.get("incident_date") == insurance.query.get("incident_date")
            and correspondent.query.get("insurance_result_id") == str(insurance.id)
            and correspondent.data.get("country") == "FR"
        )
    return bool(
        insurance and insurance.status == "matched"
        and correspondent and correspondent.status == "matched"
        and insurance.data.get("insurer_name")
        and insurance.data.get("insurer_country")
        and insurance.data.get("correspondent_country")
        and correspondent.query.get("insurer") == insurance.data.get("insurer_name")
        and correspondent.query.get("insurer_country") == insurance.data.get("insurer_country")
        and correspondent.query.get("country") == insurance.data.get("correspondent_country")
    )


def validate_output(output: AnalysisOutputV1, snapshot: AnalysisInputV1 | AnalysisInputV2) -> None:
    intake_fields = set(snapshot.intake.model_fields_set) | set(snapshot.intake.model_dump().keys())
    evidence_by_id = {str(item.id): item for item in snapshot.evidence}
    providers_by_id = {str(item.id): item for item in snapshot.provider_results}
    if output.media_analysis:
        media = output.media_analysis
        analyzed = {str(value) for value in media.analyzed_evidence_ids}
        if not analyzed <= evidence_by_id.keys():
            raise AnalysisError("invalid_source_ref", "Media analysis cites evidence outside this case.")
        citations = [citation for finding in [*media.observations, *media.plates, *media.damages] for citation in finding.citations]
        citations.extend(media.liability.citations)
        citations.extend(citation for vehicle in media.liability.vehicle_assessments for citation in vehicle.citations)
        if media.liability.likely_responsible != "undetermined" and not media.liability.citations:
            raise AnalysisError("unsupported_claim", "A liability hypothesis requires media citations.")
        for citation in citations:
            item = evidence_by_id.get(str(citation.evidence_id))
            if item is None or str(citation.evidence_id) not in analyzed:
                raise AnalysisError("invalid_source_ref", "Media citation was not analyzed in this case.")
            if item.mime_type.startswith("video/") != (citation.timestamp_seconds is not None):
                raise AnalysisError("invalid_source_ref", "Video citations require a timestamp; images must not have one.")

    def check_ref(ref: AnalysisSourceRef) -> None:
        if ref.kind == "intake":
            if ref.id != str(snapshot.case_id) or ref.locator not in intake_fields:
                raise AnalysisError("invalid_source_ref", "Analysis cited an intake field outside this case.")
            value = getattr(snapshot.intake, ref.locator, None)
            if value is None or value == "":
                raise AnalysisError("invalid_source_ref", "Analysis cited an empty intake field.")
        elif ref.kind == "evidence":
            cited_excerpt = any(item.source_ref == ref for item in snapshot.source_excerpts)
            if ref.id not in evidence_by_id or (ref.locator not in EVIDENCE_LOCATORS and not cited_excerpt):
                raise AnalysisError("invalid_source_ref", "Analysis cited evidence outside this case.")
        elif ref.kind in {"estimate", "voice"}:
            if not any(item.source_ref == ref for item in snapshot.source_excerpts):
                raise AnalysisError("invalid_source_ref", "Analysis cited a source outside this snapshot.")
        else:
            result = providers_by_id.get(ref.id)
            if result is None:
                raise AnalysisError("invalid_source_ref", "Analysis cited a provider result outside this case.")
            if result.provider == "vision":
                raise AnalysisError("invalid_source_ref", "Visual facts must cite their case-local evidence locator.")
            if ref.locator == "reason":
                valid = result.reason is not None
            else:
                root, dot, key = ref.locator.partition(".")
                obj = result.query if root == "query" else result.data if root == "data" else {}
                valid = bool(dot and key in obj and obj[key] is not None)
            if not valid:
                raise AnalysisError("invalid_source_ref", "Analysis cited a missing provider result field.")

    all_refs: list[AnalysisSourceRef] = []
    for proposition in output.propositions:
        all_refs.extend(proposition.source_refs)
        if proposition.assessment == "supported" and not proposition.source_refs:
            raise AnalysisError("unsupported_claim", "A supported proposition must cite a persisted source.")
        if proposition.assessment == "hypothesis" and not proposition.uncertainty_note:
            raise AnalysisError("unsupported_claim", "A hypothesis must be labeled with its uncertainty.")
    for issue in [*output.contradictions]:
        all_refs.extend(issue.source_refs)
    if output.recipient:
        all_refs.extend(output.recipient.source_refs)
    if output.amount:
        all_refs.extend(output.amount.source_refs)
    for ref in all_refs:
        check_ref(ref)

    if output.recipient:
        counterpart = next((r for r in snapshot.provider_results if r.provider == "correspondent_lookup"), None)
        insurance = next((r for r in snapshot.provider_results if r.provider == "insurance_lookup"), None)
        expected_ref = AnalysisSourceRef(
            kind="provider_result", id=str(counterpart.id) if counterpart else "missing",
            locator="data.correspondent_name",
        )
        expected_country_ref = AnalysisSourceRef(
            kind="provider_result", id=str(counterpart.id) if counterpart else "missing",
            locator="data.country",
        )
        if (counterpart is None or counterpart.status != "matched"
                or not correspondent_query_matches(insurance, counterpart)
                or output.recipient.name != counterpart.data.get("correspondent_name")
                or output.recipient.country != counterpart.data.get("country")
                or expected_ref not in output.recipient.source_refs
                or expected_country_ref not in output.recipient.source_refs):
            raise AnalysisError("unverified_recipient", "Recipient does not match a persisted matched correspondent result.")
    if output.amount:
        if isinstance(snapshot, AnalysisInputV2):
            estimate = snapshot.estimate
            expected = AnalysisSourceRef(kind="estimate", id=str(estimate.id) if estimate else "missing", locator="total_minor")
            if (estimate is None or output.amount.amount_minor != estimate.total_minor
                    or output.amount.currency != estimate.currency
                    or expected not in output.amount.source_refs):
                raise AnalysisError("unverified_amount", "A proposed amount must cite the current case estimate total.")
            return
        estimates = [item for item in snapshot.provider_results if item.provider == "cost_estimate" and item.status == "matched"]
        verified = any(
            any(ref.kind == "provider_result" and ref.id == str(item.id) for ref in output.amount.source_refs)
            and item.data.get("amount_minor") == output.amount.amount_minor
            and item.data.get("currency") == output.amount.currency
            for item in estimates
        )
        if not verified:
            raise AnalysisError("unverified_amount", "A proposed amount requires a persisted matched cost estimate.")


def evaluate_gates(output: AnalysisOutputV1, snapshot: AnalysisInputV1 | AnalysisInputV2) -> list[AnalysisGateResult]:
    intake = snapshot.intake
    required = ("insured_reference", "incident_at", "location", "danger_status", "injury_status")
    missing = [name for name in required if getattr(intake, name) is None or getattr(intake, name) == ""]
    if missing:
        gate1 = AnalysisGateResult(gate="intake", status="blocked", reason_codes=[f"missing_{x}" for x in missing])
    elif (intake.danger_status == "yes" or intake.injury_status == "yes"
          or isinstance(snapshot, AnalysisInputV2) and snapshot.voice_session is not None
          and snapshot.voice_session.status == "urgent_human_handoff"):
        gate1 = AnalysisGateResult(gate="intake", status="needs_review", reason_codes=["reported_immediate_danger_or_injury"])
    else:
        gate1 = AnalysisGateResult(gate="intake", status="passed", reason_codes=[])

    insurance = next((r for r in snapshot.provider_results if r.provider == "insurance_lookup"), None)
    correspondent = next((r for r in snapshot.provider_results if r.provider == "correspondent_lookup"), None)
    association = snapshot.counterparty_lookup if isinstance(snapshot, AnalysisInputV2) else None
    association_ok = bool(
        association and association.current and insurance
        and association.insurance_result_id == insurance.id
        and association.vehicle_track_id == insurance.query.get("vehicle_track_id")
        and association.plate_candidate == insurance.query.get("plate_candidate")
        and association.identification_status in {"observed", "human_confirmed"}
        and association.supporting_source_refs
        and {(ref.kind, ref.id, ref.locator) for ref in association.supporting_source_refs}
            == {(ref.get("kind"), ref.get("id"), ref.get("locator"))
                for ref in insurance.query.get("supporting_source_refs", []) if isinstance(ref, dict)}
        and all(any(excerpt.source_ref == ref and ref.kind == "evidence" and
                    ref.locator.startswith(("video:", "image:"))
                    for excerpt in snapshot.source_excerpts)
                for ref in association.supporting_source_refs)
    )
    incident_date = intake.incident_at.date().isoformat() if intake.incident_at else None
    insurance_ok = bool(
        insurance and insurance.status == "matched" and insurance.data.get("coverage") == "matched"
        and insurance.data.get("valid_on_incident_date") is True
        and insurance.query.get("incident_date") == incident_date
        and insurance.source_version == MOCK_INSURANCE_SOURCE_VERSION
        and association_ok
    )
    recipient_ok = bool(
        correspondent and correspondent.status == "matched"
        and association and association.correspondent_result_id == correspondent.id
        and correspondent_query_matches(insurance, correspondent)
        and output.recipient and output.recipient.name == correspondent.data.get("correspondent_name")
        and output.recipient.country == correspondent.data.get("country")
    )
    if insurance and insurance.status == "ambiguous":
        gate2 = AnalysisGateResult(gate="counterparty", status="blocked", reason_codes=["ambiguous_vehicle_or_coverage"])
    elif not association_ok:
        gate2 = AnalysisGateResult(gate="counterparty", status="blocked", reason_codes=["corroborated_vehicle_association_required"])
    elif not insurance_ok:
        gate2 = AnalysisGateResult(gate="counterparty", status="blocked", reason_codes=["dated_matched_coverage_required"])
    elif correspondent and correspondent.status == "matched" and not correspondent_query_matches(insurance, correspondent):
        gate2 = AnalysisGateResult(gate="counterparty", status="blocked", reason_codes=["correspondent_query_mismatch"])
    elif not recipient_ok:
        gate2 = AnalysisGateResult(gate="counterparty", status="blocked", reason_codes=["matched_correspondent_required"])
    else:
        refs = [
            AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="query.plate_candidate"),
            AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="query.incident_date"),
            *([AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="query.vehicle_track_id"),
               AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="query.identification_status"),
               AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="query.supporting_source_refs")]
              if insurance.source_version == MOCK_INSURANCE_SOURCE_VERSION else []),
            AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="data.coverage"),
            AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="data.valid_on_incident_date"),
            AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="data.insurer_name"),
            *([AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="data.insurer_id"),
               AnalysisSourceRef(kind="provider_result", id=str(correspondent.id), locator="query.insurer_id"),
               AnalysisSourceRef(kind="provider_result", id=str(correspondent.id), locator="query.accident_country"),
               AnalysisSourceRef(kind="provider_result", id=str(correspondent.id), locator="query.incident_date")]
              if insurance.source_version == MOCK_INSURANCE_SOURCE_VERSION and correspondent else [
               AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="data.insurer_country"),
               AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="data.correspondent_country"),
               *([AnalysisSourceRef(kind="provider_result", id=str(correspondent.id), locator="query.insurer"),
                  AnalysisSourceRef(kind="provider_result", id=str(correspondent.id), locator="query.insurer_country"),
                  AnalysisSourceRef(kind="provider_result", id=str(correspondent.id), locator="query.country")]
                 if correspondent else [])]),
            *(output.recipient.source_refs if output.recipient else []),
        ]
        gate2 = AnalysisGateResult(gate="counterparty", status="passed", reason_codes=[], source_refs=refs)

    has_supported_fact = any(p.assessment == "supported" and p.source_refs for p in output.propositions)
    unsupported_hypotheses = any(p.assessment == "hypothesis" and not p.source_refs
                                 for p in output.propositions)
    contradicted_propositions = any(p.assessment == "contradicted" for p in output.propositions)
    visual_refs = {excerpt.source_ref.model_dump_json() for excerpt in snapshot.source_excerpts
                   if excerpt.source_ref.kind == "evidence" and
                   excerpt.source_ref.locator.startswith(("video:", "image:")) and
                   any(marker in excerpt.text for marker in ("[movement; observed]", "[damage; observed]"))}
    corroborated = any(ref.model_dump_json() in visual_refs for proposition in output.propositions
                       if proposition.assessment == "supported" for ref in proposition.source_refs)
    amount_mismatch = bool(
        isinstance(snapshot, AnalysisInputV2) and snapshot.estimate and
        (output.amount is None or output.amount.amount_minor != snapshot.estimate.total_minor
         or output.amount.currency != snapshot.estimate.currency)
    )
    quote_mismatch = bool(isinstance(snapshot, AnalysisInputV2) and snapshot.quote and
                          snapshot.quote_status != "matched")
    g1_observation_missing = snapshot.scenario_id == "g1" and not corroborated
    review = bool(
        output.media_analysis or not has_supported_fact or output.contradictions or output.missing_items
        or unsupported_hypotheses or contradicted_propositions
        or g1_observation_missing or amount_mismatch or quote_mismatch
    )
    gate3_reasons = [x for x, present in (
        ("visual_assessment_requires_review", output.media_analysis is not None),
        ("supported_proposition_required", not has_supported_fact),
        ("contradictions_present", bool(output.contradictions)),
        ("missing_items_present", bool(output.missing_items)),
        ("unsupported_hypotheses", unsupported_hypotheses),
        ("contradicted_propositions", contradicted_propositions),
        ("visual_corroboration_required", g1_observation_missing),
        ("estimate_amount_mismatch", amount_mismatch),
        ("quote_mismatch", quote_mismatch),
    ) if present]
    gate3 = AnalysisGateResult(
        gate="evidence", status="needs_review" if review else "passed",
        reason_codes=gate3_reasons,
        source_refs=[ref for prop in output.propositions for ref in prop.source_refs],
    )
    return [gate1, gate2, gate3]


def fixture_output(snapshot: AnalysisInputV1) -> AnalysisOutputV1:
    intake_ref = lambda field: AnalysisSourceRef(kind="intake", id=str(snapshot.case_id), locator=field)
    insurance = next((r for r in snapshot.provider_results if r.provider == "insurance_lookup"), None)
    if snapshot.scenario_id == "complete" and insurance and insurance.status == "matched":
        return AnalysisOutputV1(
            schema_version=1,
            proposed_route="subrogation",
            recipient=AnalysisRecipient(
                name="Bureau Français Demo Claims Desk (fictional)", country="FR",
                source_refs=[
                    AnalysisSourceRef(kind="provider_result", id=str(next(r.id for r in snapshot.provider_results if r.provider == "correspondent_lookup")), locator="data.correspondent_name"),
                    AnalysisSourceRef(kind="provider_result", id=str(next(r.id for r in snapshot.provider_results if r.provider == "correspondent_lookup")), locator="data.country"),
                ],
            ),
            propositions=[
                AnalysisProposition(text="The insured reports a hit-and-run involving a UK-registered vehicle.", assessment="supported", source_refs=[intake_ref("narrative")]),
                AnalysisProposition(text="A persisted mock lookup reports coverage matched for the incident date and one vehicle candidate.", assessment="supported", source_refs=[AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="query.plate_candidate"), AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="query.incident_date"), AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="data.coverage"), AnalysisSourceRef(kind="provider_result", id=str(insurance.id), locator="data.valid_on_incident_date")]),
            ],
            non_blocking_notes=["Optional photo or CCTV corroboration may help review but is not required for these gates."],
            draft_body="Synthetic subrogation inquiry. The insured reports that their vehicle was struck in France by a UK-registered vehicle that left the scene. The persisted demo lookup reports matched coverage for the incident date. Please review the supporting records and confirm the appropriate handling.",
        )
    return AnalysisOutputV1(
        schema_version=1, proposed_route="insufficient_information", recipient=None,
        propositions=[AnalysisProposition(
            text="The synthetic intake reports that vehicle details are unclear.", assessment="supported",
            source_refs=[intake_ref("narrative")],
        )],
        missing_items=["A uniquely identified vehicle and dated coverage result are not available."],
        draft_body="Handler review required. The synthetic fixture does not establish a unique vehicle or valid coverage result.",
    )


class FixtureClaimsAnalyzer:
    """Deterministic adapter for tests and local review; never selected by the API runtime."""
    mode: Literal["mock"] = "mock"
    method_version = "fixture-analysis-v1"

    def analyze(self, analysis_input: AnalysisInputV1) -> AnalysisOutputV1:
        return fixture_output(analysis_input)


class PipelexClaimsAnalyzer:
    mode: Literal["live"] = "live"
    method_version = METHOD_VERSION

    def __init__(self, method_path: str | None = None, timeout_seconds: float = 45) -> None:
        from pathlib import Path
        self.method_path = Path(method_path) if method_path else Path(__file__).parent / "methods" / "claims_analysis_v2.mthds"
        self.timeout_seconds = timeout_seconds

    def analyze(self, analysis_input: AnalysisInputV1) -> AnalysisOutputV1:
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise AnalysisError("pipelex_not_configured", "Live analysis requires the server-side OPENAI_API_KEY.")
        try:
            result = asyncio.run(asyncio.wait_for(self._execute(analysis_input), timeout=self.timeout_seconds))
            return AnalysisOutputV1.model_validate(result)
        except AnalysisError:
            raise
        except TimeoutError as error:
            raise AnalysisError("analysis_timeout", "Live analysis exceeded its time limit. Retry the analysis.") from error
        except Exception as error:
            if _provider_auth_rejected(error):
                raise AnalysisError(
                    "openai_auth_failed",
                    "OpenAI rejected the server-side API key. Replace OPENAI_API_KEY in the API environment and retry.",
                ) from error
            raise AnalysisError("pipelex_failed", "Live analysis could not be completed. Check API configuration and method logs.") from error

    async def _execute(self, analysis_input: AnalysisInputV1) -> dict[str, Any]:
        _ensure_pipelex_home_is_writable()
        from pipelex.pipelex import Pipelex
        from pipelex.pipeline.runner import PipelexMTHDSProtocol
        global _PIPELEX_BOOTED
        if Pipelex.is_fully_booted():
            _PIPELEX_BOOTED = True
        if not _PIPELEX_BOOTED:
            with _PIPELEX_BOOT_LOCK:
                if not _PIPELEX_BOOTED and not Pipelex.is_fully_booted():
                    from claim_api.pipelex_luna import ClaimsInferenceManager
                    Pipelex.make(inference_manager=ClaimsInferenceManager())
                _PIPELEX_BOOTED = True
        protocol = PipelexMTHDSProtocol()
        response = await protocol.execute(
            mthds_contents=[self.method_path.read_text(encoding="utf-8")],
            inputs={"analysis_input_json": analysis_input.model_dump_json()},
        )
        content = response.pipe_output.main_stuff.content
        if hasattr(content, "smart_dump"):
            return content.smart_dump()
        if isinstance(content, str):
            return json.loads(content)
        if isinstance(content, dict):
            return content
        raise TypeError("Pipelex returned no structured analysis object")


def _provider_auth_rejected(error: BaseException) -> bool:
    """Find an OpenAI 401 through Pipelex's nested exception wrappers."""
    seen: set[int] = set()
    pending = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if type(current).__name__ == "AuthenticationError" and getattr(current, "status_code", None) == 401:
            return True
        pending.extend(cause for cause in (current.__cause__, current.__context__) if cause is not None)
    return False


def _analysis_span(analyzer: ClaimsAnalyzer):
    """Trace only bounded method metadata; never attach case content or identifiers."""
    from claim_api.agent_tracing import agent_span

    provider = getattr(analyzer, "provider", "openai" if analyzer.mode == "live" else "fixture")
    model = getattr(analyzer, "model", "gpt-4o-mini" if analyzer.mode == "live" else "fixture-analysis-v1")
    return agent_span(
        "claims.analysis",
        agent_name="Claimroom claims analysis",
        version=analyzer.method_version,
        provider=provider,
        analysis_mode=analyzer.mode,
        method_version=analyzer.method_version,
        model=model,
    )


def _ensure_pipelex_home_is_writable() -> None:
    """Point Pipelex's global config directory at writable temp storage on serverless hosts."""
    home = Path.home()
    if os.access(home, os.W_OK):
        return

    # Pipelex initializes ~/.pipelex during its first boot. Vercel's function image
    # has a read-only HOME, while /tmp is writable and survives for the instance.
    fallback_home = Path(tempfile.gettempdir()) / "claimroom-pipelex-home"
    fallback_home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(fallback_home)


@dataclass
class AnalysisService:
    repository: AnalysisRepository
    analyzer: ClaimsAnalyzer

    def run(self, actor_id: str, case_id: UUID, request: AnalysisRunRequest) -> AnalysisRunResponse:
        actor_uuid = UUID(actor_id)
        case = self.repository.get_case(actor_uuid, case_id)
        if case is None:
            from claim_api.case_service import CaseNotFoundError
            raise CaseNotFoundError
        if case.state_version != request.expected_state_version:
            from claim_api.case_service import StaleCaseError
            raise StaleCaseError(case.state_version)
        snapshot = build_analysis_input(case)
        selector = getattr(self.analyzer, "for_input", None)
        analyzer = selector(snapshot) if selector is not None else self.analyzer
        run_id = uuid4()
        input_json = snapshot.model_dump(mode="json")
        captured_revision = self.repository.begin_analysis(
            actor_uuid, case.id, request.expected_state_version, run_id,
            analyzer.method_version, analyzer.mode, input_json,
        )
        if captured_revision < 0:
            from claim_api.case_service import CaseNotFoundError
            raise CaseNotFoundError
        if captured_revision != snapshot.content_revision:
            from claim_api.case_service import StaleCaseError
            raise StaleCaseError(case.state_version)
        output: AnalysisOutputV1 | None = None
        gates: list[AnalysisGateResult] = []
        draft: dict[str, Any] | None = None
        run_status = "ready"
        error_code: str | None = None
        error_message: str | None = None
        try:
            with _analysis_span(analyzer):
                output = analyzer.analyze(snapshot)
            validate_output(output, snapshot)
            gates = evaluate_gates(output, snapshot)
            can_draft = all(item.status == "passed" for item in gates) and output.proposed_route == "subrogation"
            if can_draft:
                recipient = output.recipient.model_dump(mode="json") if output.recipient else None
                amount_minor = output.amount.amount_minor if output.amount else None
                currency = output.amount.currency if output.amount else None
                draft = {"recipient": recipient, "amount_minor": amount_minor,
                         "currency": currency, "body": output.draft_body}
        except AnalysisError as error:
            run_status, error_code, error_message = "failed", error.code, str(error)
            output, gates, draft = None, [], None
        except Exception as error:
            run_status, error_code, error_message = "failed", "analysis_failed", "Analysis failed unexpectedly; retry or contact support."
            output, gates, draft = None, [], None
        updated = self.repository.complete_analysis(
            actor_uuid, case.id, run_id, snapshot.content_revision,
            status=run_status, mode=analyzer.mode, output=output, gates=gates,
            error_code=error_code, error_message=error_message, draft=draft,
        )
        if updated is None:
            from claim_api.case_service import CaseNotFoundError
            raise CaseNotFoundError
        if updated.latest_analysis is None:
            raise RuntimeError("Analysis repository did not return the completed run.")
        return AnalysisRunResponse(analysis_run=updated.latest_analysis, case=updated)
