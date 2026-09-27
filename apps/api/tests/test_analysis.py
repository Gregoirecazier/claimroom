from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from claim_api.analysis import (
    AnalysisError,
    AnalysisService,
    FixtureClaimsAnalyzer,
    PipelexClaimsAnalyzer,
    build_analysis_input,
    evaluate_gates,
    fixture_output,
    validate_output,
)
from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.case_service import StaleCaseError
from claim_api.fixtures import get_scenario
from claim_api.gemini_analysis import GeminiClaimsAnalyzer
from claim_api.hybrid_analysis import HybridClaimsAnalyzer
from claim_api.main import create_app
from claim_api.models import (
    AnalysisAmount,
    AnalysisOutputV1,
    AnalysisRecipient,
    AnalysisRunRequest,
    AnalysisRunView,
    AnalysisSourceRef,
    AuditEventView,
    CaseView,
    CounterpartyLookupView,
    DraftView,
    EvidenceView,
    EstimateItem,
    EstimateView,
    ProviderResultView,
    QuoteView,
    VideoAnalysisLinkView,
)
from claim_api.mock_insurance import SOURCE_VERSION as MOCK_INSURANCE_SOURCE_VERSION
from claim_api.video_reuse import configured_pipeline
from claim_api.routes.analysis import get_analysis_service


ACTOR = UUID("00000000-0000-4000-8000-000000000042")


def make_case(scenario_id: str = "complete") -> CaseView:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    fixture = get_scenario(scenario_id)
    providers = []
    for item in fixture.provider_results:
        result_id = uuid4()
        providers.append(ProviderResultView(
            id=result_id, source_id=result_id, provider=item.provider, mode="mock",
            status=item.status, source_version="fixture-v1",
            query_hash=hashlib.sha256(json.dumps(item.query, sort_keys=True).encode()).hexdigest(),
            query=item.query, retrieved_at=now, data=item.data, reason=item.reason,
        ))
    return CaseView(
        id=uuid4(), created_by_user_id=ACTOR, scenario_id=scenario_id, synthetic=True,
        status="collecting", state_version=1, content_revision=1, created_at=now,
        updated_at=now, intake=fixture.intake, provider_results=providers,
        timeline=[AuditEventView(
            id=uuid4(), actor_user_id=ACTOR, event_type="case.created",
            state_version_before=0, state_version_after=1, content_revision_before=0,
            content_revision_after=1, metadata={}, occurred_at=now,
        )],
    )


def make_verified_case() -> CaseView:
    """A case with an S14-selected plate corroborated by case-local media."""
    case = make_case("complete")
    now = case.created_at
    media_id = uuid4()
    plate_ref = AnalysisSourceRef(kind="evidence", id=str(media_id), locator="video:0-1000")
    movement_ref = AnalysisSourceRef(kind="evidence", id=str(media_id), locator="video:1000-2000")
    refs = [plate_ref, movement_ref]
    insurance, correspondent = case.provider_results
    insurance = insurance.model_copy(update={
        "source_version": MOCK_INSURANCE_SOURCE_VERSION,
        "query": {**insurance.query, "vehicle_track_id": "track-1", "identification_status": "observed",
                  "supporting_source_refs": [ref.model_dump() for ref in refs]},
        "data": {**insurance.data, "insurer_id": "demo-uk-insurer"},
    })
    correspondent = correspondent.model_copy(update={
        "source_version": MOCK_INSURANCE_SOURCE_VERSION,
        "query": {"insurer_id": "demo-uk-insurer", "accident_country": "FR",
                  "incident_date": "2025-06-14", "insurance_result_id": str(insurance.id)},
    })
    evidence = EvidenceView(
        id=media_id, case_id=case.id, kind="scene_video", storage_path="synthetic/g1.mp4",
        source_kind="synthetic_fixture", mode="mock", mime_type="video/mp4", byte_size=100,
        client_sha256=None, checksum_status="verified", received_at=now,
    )
    video = VideoAnalysisLinkView(
        evidence_id=media_id, artifact_id=uuid4(), pipeline_fingerprint=configured_pipeline().fingerprint,
        observations=[
            {"start_ms": 0, "end_ms": 1000, "category": "visible_text", "status": "observed",
             "vehicle_track_id": "track-1", "plate_candidate": "UK-SYN-482", "description": "Plate on involved car"},
            {"start_ms": 1000, "end_ms": 2000, "category": "movement", "status": "observed",
             "vehicle_track_id": "track-1", "description": "Involved car moves after contact"},
        ],
    )
    lookup = CounterpartyLookupView(
        plate_candidate="UK-SYN-482", country="UK", incident_date=case.intake.incident_at.date(),
        vehicle_track_id="track-1", identification_status="observed", supporting_source_refs=refs,
        insurance_result_id=insurance.id, correspondent_result_id=correspondent.id, current=True,
    )
    return case.model_copy(update={"provider_results": [insurance, correspondent], "evidence": [evidence],
                                   "video_analyses": [video], "counterparty_lookup": lookup})


class MemoryAnalysisRepository:
    def __init__(self, case: CaseView):
        self.case = case
        self.run_id: UUID | None = None

    def get_case(self, actor_id: UUID, case_id: UUID) -> CaseView | None:
        return self.case if actor_id == ACTOR and case_id == self.case.id else None

    def begin_analysis(self, actor_id, case_id, expected_state_version, run_id, method_version, mode, input_json):
        if self.case.state_version != expected_state_version:
            raise StaleCaseError(self.case.state_version)
        self.run_id = run_id
        self.started = AnalysisRunView(
            id=run_id, input_content_revision=self.case.content_revision,
            method_version=method_version, status="running", mode=mode,
            started_at=datetime.now(timezone.utc),
        )
        return self.case.content_revision

    def complete_analysis(self, actor_id, case_id, run_id, input_content_revision, *, status, mode, output,
                          gates, error_code, error_message, draft):
        stale = self.case.content_revision != input_content_revision
        run_status = "stale" if stale else status
        new_draft = self.case.current_draft
        new_revision = self.case.content_revision
        if not stale and draft is not None:
            new_revision += 1
            new_draft = DraftView(
                id=uuid4(), case_id=case_id, analysis_run_id=run_id,
                parent_draft_id=None, version=1,
                content_revision=new_revision, recipient=draft["recipient"],
                amount_minor=draft["amount_minor"], currency=draft["currency"],
                body=draft["body"], attachment_ids=[], sha256="a" * 64,
                created_at=datetime.now(timezone.utc),
            )
        run = AnalysisRunView(
            id=run_id, input_content_revision=input_content_revision,
            method_version=self.started.method_version, status=run_status, mode=mode,
            output=None if stale else output, gate_results=[] if stale else gates,
            error_code="stale_input_revision" if stale else error_code,
            error_message="The case changed while analysis was running." if stale else error_message,
            started_at=self.started.started_at, finished_at=datetime.now(timezone.utc),
        )
        self.case = self.case.model_copy(update={
            "state_version": self.case.state_version + 1,
            "content_revision": new_revision,
            "status": "review_ready" if new_draft else "collecting",
            "latest_analysis": run,
            "gate_results": run.gate_results,
            "current_draft": new_draft,
        })
        return self.case


def test_complete_fixture_output_is_source_checked_and_passes_all_gates() -> None:
    case = make_verified_case()
    snapshot = build_analysis_input(case)
    output = fixture_output(snapshot)

    validate_output(output, snapshot)
    gates = evaluate_gates(output, snapshot)

    assert [item.status for item in gates] == ["passed", "passed", "passed"]
    assert case.intake.injury_status == "no"
    assert "UK-SYN-482" in (case.intake.narrative or "")
    assert output.missing_items == []
    assert output.non_blocking_notes
    assert all(ref.id == str(case.id) or any(str(provider.id) == ref.id for provider in case.provider_results)
               for gate in gates for ref in gate.source_refs)


def test_narrative_plate_without_selected_media_track_cannot_create_draft() -> None:
    repository = MemoryAnalysisRepository(make_case("complete"))
    result = AnalysisService(repository, FixtureClaimsAnalyzer()).run(
        str(ACTOR), repository.case.id, AnalysisRunRequest(expected_state_version=1),
    )
    assert result.analysis_run.status == "ready"
    assert result.analysis_run.gate_results[1].reason_codes == ["corroborated_vehicle_association_required"]
    assert result.case.current_draft is None


def test_g1_requires_an_observation_cited_by_supported_proposition() -> None:
    case = make_verified_case().model_copy(update={"scenario_id": "g1"})
    snapshot = build_analysis_input(case)
    output = fixture_output(build_analysis_input(case.model_copy(update={"scenario_id": "complete"})))
    assert evaluate_gates(output, snapshot)[2].reason_codes == ["visual_corroboration_required"]
    plate_only = output.model_copy(update={"propositions": [
        output.propositions[0].model_copy(update={"source_refs": [case.counterparty_lookup.supporting_source_refs[0]]}),
        *output.propositions[1:],
    ]})
    assert "visual_corroboration_required" in evaluate_gates(plate_only, snapshot)[2].reason_codes
    visual_ref = case.counterparty_lookup.supporting_source_refs[1]
    supported = output.propositions[0].model_copy(update={"source_refs": [visual_ref]})
    corroborated = output.model_copy(update={"propositions": [supported, *output.propositions[1:]]})
    validate_output(corroborated, snapshot)
    assert evaluate_gates(corroborated, snapshot)[2].status == "passed"


def test_current_estimate_requires_sourced_amount_and_matched_quote() -> None:
    case = make_verified_case()
    estimate = EstimateView(
        id=uuid4(), case_id=case.id, version=1,
        line_items=[EstimateItem(id="repair", label="Repair", amount_minor=12345)],
        total_minor=12345, currency="EUR", tax_basis="TTC", estimate_source="handler",
        created_by_user_id=ACTOR, created_at=case.created_at,
    )
    case = case.model_copy(update={"estimate": estimate, "quote_status": "no_quote"})
    snapshot = build_analysis_input(case)
    output = fixture_output(snapshot)
    assert "estimate_amount_mismatch" in evaluate_gates(output, snapshot)[2].reason_codes
    amount = AnalysisAmount(amount_minor=12345, currency="EUR", source_refs=[
        AnalysisSourceRef(kind="estimate", id=str(estimate.id), locator="total_minor"),
    ])
    sourced = output.model_copy(update={"amount": amount})
    validate_output(sourced, snapshot)
    assert evaluate_gates(sourced, snapshot)[2].status == "passed"

    quote = QuoteView(
        id=uuid4(), case_id=case.id, version=1, evidence_id=case.evidence[0].id,
        filename="synthetic.pdf", mime_type="application/pdf", checksum="a" * 64,
        checksum_status="verified", total_ttc_minor=99999, amount_source="handler_entered",
        attached_estimate_version=1, created_by_user_id=ACTOR, created_at=case.created_at,
    )
    conflicting = build_analysis_input(case.model_copy(update={"quote": quote, "quote_status": "mismatch"}))
    assert "quote_mismatch" in evaluate_gates(sourced, conflicting)[2].reason_codes


def test_gate_three_requires_a_supported_sourced_proposition_before_draft() -> None:
    snapshot = build_analysis_input(make_case("complete"))
    empty = fixture_output(snapshot).model_copy(update={"propositions": []})
    validate_output(empty, snapshot)
    gate = evaluate_gates(empty, snapshot)[2]
    assert gate.status == "needs_review"
    assert gate.reason_codes == ["supported_proposition_required"]

    class EmptySummaryAnalyzer(FixtureClaimsAnalyzer):
        def analyze(self, analysis_input):
            return fixture_output(analysis_input).model_copy(update={"propositions": []})

    repository = MemoryAnalysisRepository(make_case("complete"))
    result = AnalysisService(repository, EmptySummaryAnalyzer()).run(
        str(ACTOR), repository.case.id, AnalysisRunRequest(expected_state_version=1),
    )
    assert result.analysis_run.gate_results[2].status == "needs_review"
    assert result.case.current_draft is None


def test_sourced_liability_hypothesis_can_be_reviewed_but_unsourced_one_blocks() -> None:
    snapshot = build_analysis_input(make_verified_case())
    output = fixture_output(snapshot)
    hypothesis = output.propositions[0].model_copy(update={
        "text": "The observed movement may support a liability argument; legal basis requires review.",
        "assessment": "hypothesis",
        "source_refs": [snapshot.counterparty_lookup.supporting_source_refs[1]],
        "uncertainty_note": "Collision dynamics and legal responsibility require handler review.",
    })
    sourced = output.model_copy(update={"propositions": [*output.propositions, hypothesis]})
    validate_output(sourced, snapshot)
    assert evaluate_gates(sourced, snapshot)[2].status == "passed"
    unsupported = sourced.model_copy(update={"propositions": [*output.propositions,
        hypothesis.model_copy(update={"source_refs": []})]})
    assert "unsupported_hypotheses" in evaluate_gates(unsupported, snapshot)[2].reason_codes


def test_ambiguous_fixture_blocks_counterparty_even_without_model_recipient() -> None:
    snapshot = build_analysis_input(make_case("ambiguous"))
    output = fixture_output(snapshot)

    validate_output(output, snapshot)
    gates = evaluate_gates(output, snapshot)

    assert gates[0].status == "passed"
    assert gates[1].status == "blocked"
    assert "ambiguous_vehicle_or_coverage" in gates[1].reason_codes
    assert gates[2].status == "needs_review"


@pytest.mark.parametrize("query_patch", [
    {"insurer_id": "different-insurer"},
    {"accident_country": "GB"},
    {"incident_date": "2025-06-15"},
])
def test_gate_two_blocks_correspondent_lookup_with_mismatched_insurer_or_country(query_patch) -> None:
    case = make_verified_case()
    providers = []
    for result in case.provider_results:
        if result.provider == "correspondent_lookup":
            providers.append(result.model_copy(update={"query": {**result.query, **query_patch}}))
        else:
            providers.append(result)
    snapshot = build_analysis_input(case.model_copy(update={"provider_results": providers}))
    output = fixture_output(snapshot)

    with pytest.raises(AnalysisError, match="Recipient does not match"):
        validate_output(output, snapshot)
    gate = evaluate_gates(output, snapshot)[1]
    assert gate.status == "blocked"
    assert gate.reason_codes == ["correspondent_query_mismatch"]


def test_model_cannot_invent_sources_or_override_ambiguous_recipient() -> None:
    case = make_case("ambiguous")
    case = case.model_copy(update={
        "provider_results": [
            item.model_copy(update={"data": {"correspondent_name": "Possible Demo Desk", "country": "GB"}})
            if item.provider == "correspondent_lookup" else item
            for item in case.provider_results
        ],
    })
    snapshot = build_analysis_input(case)
    output = fixture_output(snapshot)
    forged = output.model_copy(update={
        "propositions": [output.propositions[0].model_copy(update={
            "source_refs": [AnalysisSourceRef(kind="provider_result", id=str(uuid4()), locator="data.coverage")],
        })],
    })
    with pytest.raises(AnalysisError, match="outside this case"):
        validate_output(forged, snapshot)

    correspondence = next(item for item in snapshot.provider_results if item.provider == "correspondent_lookup")
    suggested = output.model_copy(update={
        "recipient": AnalysisRecipient(
            name="Invented recipient", country="GB",
            source_refs=[
                AnalysisSourceRef(kind="provider_result", id=str(correspondence.id), locator="data.correspondent_name"),
                AnalysisSourceRef(kind="provider_result", id=str(correspondence.id), locator="data.country"),
            ],
        ),
    })
    with pytest.raises(AnalysisError, match="Recipient does not match"):
        validate_output(suggested, snapshot)


def test_recipient_requires_citations_for_both_persisted_fields() -> None:
    snapshot = build_analysis_input(make_case("complete"))
    output = fixture_output(snapshot)
    name_only = output.model_copy(update={
        "recipient": output.recipient.model_copy(update={"source_refs": output.recipient.source_refs[:1]}),
    })

    with pytest.raises(AnalysisError, match="Recipient does not match"):
        validate_output(name_only, snapshot)


def test_proposed_amount_must_match_persisted_cost_estimate() -> None:
    case = make_verified_case()
    estimate_id = uuid4()
    estimate = EstimateView(
        id=estimate_id, case_id=case.id, version=1,
        line_items=[EstimateItem(id="repair", label="Repair", amount_minor=12345)],
        total_minor=12345, currency="EUR", tax_basis="TTC", estimate_source="handler",
        created_by_user_id=ACTOR, created_at=datetime.now(timezone.utc),
    )
    case = case.model_copy(update={"estimate": estimate})
    snapshot = build_analysis_input(case)
    output = fixture_output(snapshot).model_copy(update={
        "amount": AnalysisAmount(
            amount_minor=12346, currency="EUR",
            source_refs=[AnalysisSourceRef(kind="estimate", id=str(estimate_id), locator="total_minor")],
        ),
    })

    with pytest.raises(AnalysisError, match="current case estimate"):
        validate_output(output, snapshot)


def test_pipelex_analyzer_fails_clearly_without_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AnalysisError, match="server-side OPENAI_API_KEY") as error:
        PipelexClaimsAnalyzer().analyze(build_analysis_input(make_case()))
    assert error.value.code == "pipelex_not_configured"


def test_hybrid_analysis_selects_text_or_media_and_records_selected_method() -> None:
    class MediaAnalyzer:
        mode = "live"
        method_version = "test-media-v1"

        def analyze(self, analysis_input):
            return fixture_output(analysis_input)

    hybrid = HybridClaimsAnalyzer(text_analyzer=FixtureClaimsAnalyzer(), media_analyzer=MediaAnalyzer())
    for case, expected_method in (
        (make_case("complete"), FixtureClaimsAnalyzer.method_version),
        (make_verified_case(), MediaAnalyzer.method_version),
    ):
        repository = MemoryAnalysisRepository(case)
        result = AnalysisService(repository, hybrid).run(
            str(ACTOR), case.id, AnalysisRunRequest(expected_state_version=1),
        )
        assert result.analysis_run.status == "ready"
        assert result.analysis_run.method_version == expected_method


def test_hybrid_media_does_not_fall_back_when_gemini_key_is_missing() -> None:
    case = make_verified_case()
    repository = MemoryAnalysisRepository(case)
    hybrid = HybridClaimsAnalyzer(text_analyzer=FixtureClaimsAnalyzer(),
                                  media_analyzer=GeminiClaimsAnalyzer(api_key=""))

    result = AnalysisService(repository, hybrid).run(
        str(ACTOR), case.id, AnalysisRunRequest(expected_state_version=1),
    )

    assert result.analysis_run.status == "failed"
    assert result.analysis_run.error_code == "gemini_not_configured"
    assert result.analysis_run.method_version.startswith("gemini-joint-media-v2:")
    assert result.case.current_draft is None


def test_analysis_route_can_select_hybrid_mode(monkeypatch) -> None:
    import claim_api.routes.analysis as analysis_route

    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db?sslmode=require")
    monkeypatch.setenv("ANALYSIS_PROVIDER", "hybrid")
    monkeypatch.setattr(analysis_route, "PostgresCaseRepository", lambda database_url: object())

    assert isinstance(analysis_route.get_analysis_service().analyzer, HybridClaimsAnalyzer)


def test_pipelex_auth_rejection_is_recorded_with_actionable_code(monkeypatch) -> None:
    class AuthenticationError(Exception):
        status_code = 401

    async def rejected(self, analysis_input):
        try:
            raise AuthenticationError("invalid key")
        except AuthenticationError as error:
            raise RuntimeError("Pipelex pipeline failed") from error

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(PipelexClaimsAnalyzer, "_execute", rejected)
    repository = MemoryAnalysisRepository(make_case("complete"))

    result = AnalysisService(repository, PipelexClaimsAnalyzer()).run(
        str(ACTOR), repository.case.id, AnalysisRunRequest(expected_state_version=1),
    )

    assert result.analysis_run.status == "failed"
    assert result.analysis_run.mode == "live"
    assert result.analysis_run.error_code == "openai_auth_failed"
    assert "OPENAI_API_KEY" in result.analysis_run.error_message
    assert result.analysis_run.output is None
    assert result.case.current_draft is None


def test_pipelex_home_uses_writable_temp_storage_when_home_is_read_only(monkeypatch, tmp_path) -> None:
    import os
    from pathlib import Path
    import claim_api.analysis as analysis_module

    home = tmp_path / "readonly-home"
    home.mkdir()
    real_access = os.access
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        analysis_module.os,
        "access",
        lambda path, mode: False if Path(path) == home else real_access(path, mode),
    )
    monkeypatch.setattr(analysis_module.tempfile, "gettempdir", lambda: str(tmp_path))

    analysis_module._ensure_pipelex_home_is_writable()

    expected_home = tmp_path / "claimroom-pipelex-home"
    assert Path(os.environ["HOME"]) == expected_home
    assert expected_home.is_dir()


def test_pipelex_adapter_imports_and_invokes_typed_protocol_without_provider_call(monkeypatch, tmp_path) -> None:
    import claim_api.analysis as analysis_module
    import pipelex.pipelex as pipelex_module
    import pipelex.pipeline.runner as runner_module

    calls = {}
    snapshot = build_analysis_input(make_case("complete"))
    expected = fixture_output(snapshot).model_dump(mode="json")

    class Runner:
        async def execute(self, *, mthds_contents, inputs):
            calls["bundle"] = mthds_contents
            calls["inputs"] = inputs
            return SimpleNamespace(pipe_output=SimpleNamespace(main_stuff=SimpleNamespace(
                content=SimpleNamespace(smart_dump=lambda: expected),
            )))

    monkeypatch.setattr(pipelex_module.Pipelex, "make", staticmethod(lambda **kwargs: None))
    monkeypatch.setattr(runner_module, "PipelexMTHDSProtocol", Runner)
    monkeypatch.setattr(analysis_module, "_PIPELEX_BOOTED", False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    method_file = tmp_path / "method.mthds"
    method_file.write_text("domain = 'synthetic_claims'", encoding="utf-8")

    result = PipelexClaimsAnalyzer(str(method_file)).analyze(snapshot)

    assert result == fixture_output(snapshot)
    assert calls["bundle"] == [method_file.read_text(encoding="utf-8")]
    assert json.loads(calls["inputs"]["analysis_input_json"])["case_id"] == str(snapshot.case_id)


def test_pipelex_runtime_boots_once_across_sequential_analyses(monkeypatch, tmp_path) -> None:
    import claim_api.analysis as analysis_module
    import pipelex.pipelex as pipelex_module
    import pipelex.pipeline.runner as runner_module

    snapshot = build_analysis_input(make_case("complete"))
    expected = fixture_output(snapshot).model_dump(mode="json")
    boot_calls = 0

    def make_pipelex(**kwargs):
        nonlocal boot_calls
        boot_calls += 1

    class Runner:
        async def execute(self, *, mthds_contents, inputs):
            return SimpleNamespace(pipe_output=SimpleNamespace(main_stuff=SimpleNamespace(
                content=SimpleNamespace(smart_dump=lambda: expected),
            )))

    monkeypatch.setattr(pipelex_module.Pipelex, "make", staticmethod(make_pipelex))
    monkeypatch.setattr(
        pipelex_module.Pipelex,
        "is_fully_booted",
        classmethod(lambda cls: boot_calls > 0),
    )
    monkeypatch.setattr(runner_module, "PipelexMTHDSProtocol", Runner)
    monkeypatch.setattr(analysis_module, "_PIPELEX_BOOTED", False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    method_file = tmp_path / "method.mthds"
    method_file.write_text("domain = 'synthetic_claims'", encoding="utf-8")
    analyzer = PipelexClaimsAnalyzer(str(method_file))

    analyzer.analyze(snapshot)
    analyzer.analyze(snapshot)

    assert boot_calls == 1


def test_logfire_analysis_span_contains_metadata_only(monkeypatch) -> None:
    import logfire
    from opentelemetry import trace

    recorded: list[tuple[str, dict[str, str]]] = []
    semantic_attributes: dict[str, str] = {}

    @contextmanager
    def span(name: str, **attributes: str):
        recorded.append((name, attributes))
        yield

    class CurrentSpan:
        def set_attribute(self, key: str, value: str) -> None:
            semantic_attributes[key] = value

    monkeypatch.setattr(logfire, "span", span)
    monkeypatch.setattr(trace, "get_current_span", lambda: CurrentSpan())
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-only-token")
    repository = MemoryAnalysisRepository(make_case("complete"))
    AnalysisService(repository, FixtureClaimsAnalyzer()).run(
        str(ACTOR), repository.case.id, AnalysisRunRequest(expected_state_version=1),
    )

    assert recorded == [(
        "claims.analysis",
        {
            "analysis_mode": "mock",
            "method_version": "fixture-analysis-v1",
            "model": "fixture-analysis-v1",
        },
    )]
    assert semantic_attributes == {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "Claimroom claims analysis",
        "gen_ai.agent.version": "fixture-analysis-v1",
        "gen_ai.provider.name": "fixture",
        "openinference.span.kind": "AGENT",
    }


def test_analysis_service_creates_draft_only_when_all_gates_pass() -> None:
    repository = MemoryAnalysisRepository(make_verified_case())
    result = AnalysisService(repository, FixtureClaimsAnalyzer()).run(
        str(ACTOR), repository.case.id, AnalysisRunRequest(expected_state_version=1),
    )
    assert result.analysis_run.status == "ready"
    assert all(gate.status == "passed" for gate in result.analysis_run.gate_results)
    assert result.case.current_draft is not None
    assert result.case.current_draft.analysis_run_id == result.analysis_run.id

    ambiguous_repository = MemoryAnalysisRepository(make_case("ambiguous"))
    blocked = AnalysisService(ambiguous_repository, FixtureClaimsAnalyzer()).run(
        str(ACTOR), ambiguous_repository.case.id, AnalysisRunRequest(expected_state_version=1),
    )
    assert blocked.analysis_run.status == "ready"
    assert blocked.analysis_run.gate_results[1].status == "blocked"
    assert blocked.case.current_draft is None


def test_case_edit_during_analysis_persists_stale_run_without_draft() -> None:
    repository = MemoryAnalysisRepository(make_case("complete"))
    snapshot_case_id = repository.case.id

    class EditingAnalyzer(FixtureClaimsAnalyzer):
        def analyze(self, analysis_input):
            repository.case = repository.case.model_copy(update={
                "content_revision": repository.case.content_revision + 1,
                "state_version": repository.case.state_version + 1,
            })
            return fixture_output(analysis_input)

    result = AnalysisService(repository, EditingAnalyzer()).run(
        str(ACTOR), snapshot_case_id, AnalysisRunRequest(expected_state_version=1),
    )
    assert result.analysis_run.status == "stale"
    assert result.case.current_draft is None
    assert result.analysis_run.output is None


def test_analysis_route_returns_reviewable_fixture_run() -> None:
    from claim_api.routes.cases import get_case_service

    repository = MemoryAnalysisRepository(make_verified_case())
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(ACTOR))
    app.dependency_overrides[get_analysis_service] = lambda: AnalysisService(repository, FixtureClaimsAnalyzer())
    client = TestClient(app)

    response = client.post(f"/v1/cases/{repository.case.id}/analysis-runs", json={"expected_state_version": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["analysis_run"]["mode"] == "mock"
    assert body["analysis_run"]["status"] == "ready"
    assert len(body["analysis_run"]["gate_results"]) == 3
    assert body["case"]["current_draft"]["analysis_run_id"] == body["analysis_run"]["id"]
