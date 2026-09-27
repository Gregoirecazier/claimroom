from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4
import os

import pytest
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from claim_api.estimate import G1_DEMO_ITEMS, quote_status
from claim_api.fixtures import get_scenario
from claim_api.models import (
    AnalysisOutputV1, AnalysisProposition, AnalysisRunView, AnalysisSourceRef,
    CaseView, EvidenceView, EstimateView, ProviderResultView, QuoteView,
    AttachQuoteRequest, RemoveQuoteRequest, ReportLineEditRequest, UpsertEstimateRequest,
)
from claim_api.report import build_report, validate_report_refs
from claim_api.case_service import CaseService, StaleCaseError
from claim_api.case_service import merge_intake
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.main import create_app
from claim_api.routes.cases import get_case_service


ACTOR = UUID("00000000-0000-4000-8000-000000000042")
NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


def make_case() -> CaseView:
    return CaseView(
        id=uuid4(), created_by_user_id=ACTOR, scenario_id="g1", synthetic=True,
        status="collecting", state_version=1, content_revision=1,
        created_at=NOW, updated_at=NOW, intake=get_scenario("g1").intake,
    )


def test_g1_report_keeps_plate_and_damage_unknown_and_explains_time() -> None:
    case = make_case()
    report = build_report(case)
    lines = {line.id: line for line in report.lines}
    assert lines["intake.incident_at"].uncertainty == "Heure déduite de l'appel ; à confirmer."
    assert {ref.locator for ref in lines["intake.incident_at"].source_refs} == {"incident_at", "time_source"}
    assert "FR-482-KL" in lines["intake.insured_plate"].text
    assert lines["unknown.third_party_plate"].text == "Plaque de la BMW : inconnue."
    assert lines["unknown.visual_damage"].claim_kind == "hypothesis"
    assert report.analysis_current is False


def test_report_projects_four_source_kinds_and_marks_old_analysis_stale() -> None:
    case = make_case()
    evidence = EvidenceView(
        id=uuid4(), case_id=case.id, kind="scene_photo", storage_path=f"{case.id}/a.png",
        source_kind="handler_upload", mode="live", mime_type="image/png", byte_size=3,
        client_sha256="a" * 64, checksum_status="client_declared", received_at=NOW,
    )
    provider = ProviderResultView(
        id=uuid4(), source_id=uuid4(), provider="insurance_lookup", mode="mock",
        status="unavailable", source_version="v1", query_hash="a" * 64,
        query={}, data={}, retrieved_at=NOW,
    )
    output = AnalysisOutputV1(
        schema_version=1, proposed_route="insufficient_information",
        propositions=[AnalysisProposition(
            text="La dynamique du choc reste à vérifier.", assessment="hypothesis",
            source_refs=[AnalysisSourceRef(kind="intake", id=str(case.id), locator="narrative")],
            uncertainty_note="Récit seul.",
        )], draft_body="Revue nécessaire.",
    )
    run = AnalysisRunView(
        id=uuid4(), input_content_revision=1, method_version="test", status="ready",
        mode="mock", output=output, started_at=NOW, finished_at=NOW,
    )
    case = case.model_copy(update={"evidence": [evidence], "provider_results": [provider], "latest_analysis": run})
    report = build_report(case)
    assert {line.claim_kind for line in report.lines} >= {"declaration", "observation", "provider_result", "hypothesis"}
    assert report.analysis_current is True
    validate_report_refs(case, [AnalysisSourceRef(kind="evidence", id=str(evidence.id), locator="kind")])
    validate_report_refs(case, [AnalysisSourceRef(kind="provider_result", id=str(provider.id), locator="status")])
    with pytest.raises(ValueError, match="outside this case"):
        validate_report_refs(case, [AnalysisSourceRef(kind="evidence", id=str(uuid4()), locator="kind")])
    stale = build_report(case.model_copy(update={"content_revision": 2}))
    assert stale.needs_reanalysis
    assert next(line for line in stale.lines if line.id == "analysis.proposition.0").stale
    broken_output = output.model_copy(update={"propositions": [output.propositions[0].model_copy(update={
        "source_refs": [AnalysisSourceRef(kind="evidence", id=str(uuid4()), locator="kind")],
    })]})
    broken_run = run.model_copy(update={"output": broken_output})
    missing_source = build_report(case.model_copy(update={"latest_analysis": broken_run}))
    assert missing_source.analysis_current is False
    assert missing_source.needs_reanalysis
    assert next(line for line in missing_source.lines if line.id == "analysis.proposition.0").stale


def test_handler_time_edit_replaces_inferred_call_time_provenance() -> None:
    from claim_api.models import IntakePatch
    case = make_case()
    edited = merge_intake(case.intake, IntakePatch(incident_at=datetime(2026, 9, 25, 9, 30, tzinfo=timezone.utc)))
    assert edited.time_source == "handler_entered"


@pytest.mark.parametrize("amount", [-1, 1.5, float("nan"), "1.25", True])
def test_estimate_rejects_non_integer_minor_units(amount) -> None:
    with pytest.raises(ValidationError):
        UpsertEstimateRequest(
            line_items=[{"id": "repair", "label": "Réparation", "amount_minor": amount}],
            total_minor=100, currency="EUR", tax_basis="TTC", expected_state_version=1,
        )


def test_estimate_total_is_api_calculated_and_quote_version_is_checked() -> None:
    assert sum(item.amount_minor for item in G1_DEMO_ITEMS) == 124_000
    with pytest.raises(ValidationError):
        UpsertEstimateRequest(
            line_items=G1_DEMO_ITEMS, total_minor=123_999,
            currency="EUR", tax_basis="TTC", expected_state_version=1,
        )
    with pytest.raises(ValidationError):
        UpsertEstimateRequest(
            line_items=G1_DEMO_ITEMS, total_minor=124_000,
            currency="USD", tax_basis="TTC", expected_state_version=1,
        )
    estimate = EstimateView(
        id=uuid4(), case_id=uuid4(), version=2, line_items=G1_DEMO_ITEMS,
        total_minor=124_000, currency="EUR", tax_basis="TTC", estimate_source="demo_fixture",
        created_by_user_id=None, created_at=NOW,
    )
    quote = QuoteView(
        id=uuid4(), case_id=estimate.case_id, version=1, evidence_id=uuid4(),
        filename="devis.pdf", mime_type="application/pdf", checksum="a" * 64,
        checksum_status="client_declared", total_ttc_minor=124_000,
        amount_source="handler_entered", attached_estimate_version=1,
        created_by_user_id=ACTOR, created_at=NOW,
    )
    assert quote_status(estimate, quote) == "outdated"
    assert quote_status(estimate, quote.model_copy(update={"attached_estimate_version": 2})) == "matched"
    assert quote_status(estimate, quote.model_copy(update={"attached_estimate_version": 2, "total_ttc_minor": 123_000})) == "mismatch"


def test_persisted_report_estimate_and_quote_versions(monkeypatch) -> None:
    database_url = os.getenv("TEST_MIGRATION_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_MIGRATION_DATABASE_URL to a disposable loopback PostgreSQL test database")
    parsed = make_url(database_url)
    if parsed.host not in {"localhost", "127.0.0.1", "::1"} or "test" not in (parsed.database or "").lower():
        pytest.fail("TEST_MIGRATION_DATABASE_URL must target a loopback test database")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_ALLOW_INSECURE_LOCAL", "true")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", database_url)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text("create schema if not exists auth"))
        connection.execute(text("create table if not exists auth.users (id uuid primary key)"))
    command.upgrade(Config(str(__import__("pathlib").Path(__file__).parents[1] / "alembic.ini")), "head")
    actor_id, other_actor_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(text("insert into auth.users (id) values (:id), (:other_id)"),
                           {"id": str(actor_id), "other_id": str(other_actor_id)})
    repository = PostgresCaseRepository(database_url)
    service = CaseService(repository)
    first = service.create_case(str(actor_id), "g1")
    other = service.create_case(str(other_actor_id), "g1")
    assert first.estimate and first.estimate.total_minor == 124_000
    assert first.estimate.estimate_source == "demo_fixture"
    assert first.report.lines and first.report.lines[0].source_refs
    line = next(item for item in first.report.lines if item.id == "intake.location")
    edited = service.edit_report_line(str(actor_id), first.id, line.id,
        ReportLineEditRequest(text="Lieu corrigé par le gestionnaire.", uncertainty="À confirmer sur place.", expected_state_version=1))
    assert edited.content_revision == 2
    corrected = next(item for item in edited.report.lines if item.id == line.id)
    assert corrected.claim_kind == "handler_edit" and corrected.signed_by == actor_id
    assert corrected.previous_text == line.text
    assert edited.timeline[-1].metadata["changed_fields"] == ["text", "uncertainty"]
    assert "previous_text" not in edited.timeline[-1].metadata
    assert service.get_case(str(actor_id), first.id).report == edited.report
    with pytest.raises(StaleCaseError):
        service.edit_report_line(str(actor_id), first.id, line.id,
            ReportLineEditRequest(text="Édition périmée.", expected_state_version=1))

    estimate = service.upsert_estimate(str(actor_id), first.id, UpsertEstimateRequest(
        line_items=[{"id": "repair", "label": "Réparation", "amount_minor": 50_000}],
        total_minor=50_000, currency="EUR", tax_basis="TTC", expected_state_version=edited.state_version,
    ))
    assert estimate.estimate and estimate.estimate.version == 2
    assert estimate.estimate.total_minor == 50_000
    pdf_id, foreign_pdf_id = uuid4(), uuid4()
    with engine.begin() as connection:
        for case_id, evidence_id in [(first.id, pdf_id), (other.id, foreign_pdf_id)]:
            connection.execute(text("""insert into public.evidence
                (id, case_id, storage_path, kind, source_kind, mode, mime_type,
                 byte_size, client_sha256, checksum_status, original_filename)
                values (:id, :case_id, :path, 'document', 'handler_upload', 'live',
                        'application/pdf', 100, :sha, 'client_declared', 'devis.pdf')"""), {
                "id": str(evidence_id), "case_id": str(case_id),
                "path": f"{case_id}/uploads/{evidence_id}.pdf", "sha": "a" * 64,
            })
    with pytest.raises(ValueError, match="belonging to this case"):
        service.attach_quote(str(actor_id), first.id, AttachQuoteRequest(
            evidence_id=foreign_pdf_id, total_ttc_minor=45_000,
            expected_state_version=estimate.state_version,
        ))
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(actor_id))
    app.dependency_overrides[get_case_service] = lambda: service
    client = TestClient(app)
    forbidden_pdf = client.put(f"/v1/cases/{first.id}/quote", json={
        "evidence_id": str(foreign_pdf_id), "total_ttc_minor": 45_000,
        "expected_state_version": estimate.state_version,
    })
    assert forbidden_pdf.status_code == 422
    assert forbidden_pdf.json()["error"]["code"] == "invalid_case_material"
    invalid_total = client.put(f"/v1/cases/{first.id}/estimate", json={
        "line_items": [{"id": "repair", "label": "Réparation", "amount_minor": 50_000}],
        "total_minor": 49_999, "currency": "EUR", "tax_basis": "TTC",
        "expected_state_version": estimate.state_version,
    })
    assert invalid_total.status_code == 422
    assert invalid_total.json()["error"]["code"] == "invalid_request"
    mismatched = service.attach_quote(str(actor_id), first.id, AttachQuoteRequest(
        evidence_id=pdf_id, total_ttc_minor=45_000, expected_state_version=estimate.state_version,
    ))
    assert mismatched.quote and mismatched.quote_status == "mismatch"
    assert mismatched.quote.filename == "devis.pdf"
    assert repository.get_owned_evidence(other_actor_id, first.id, pdf_id) is None
    assert repository.get_owned_evidence(actor_id, first.id, pdf_id) is not None
    new_estimate = service.upsert_estimate(str(actor_id), first.id, UpsertEstimateRequest(
        line_items=[{"id": "repair", "label": "Réparation", "amount_minor": 45_000}],
        total_minor=45_000, currency="EUR", tax_basis="TTC",
        expected_state_version=mismatched.state_version,
    ))
    assert new_estimate.quote_status == "outdated"
    aligned = service.attach_quote(str(actor_id), first.id, AttachQuoteRequest(
        evidence_id=pdf_id, total_ttc_minor=45_000, expected_state_version=new_estimate.state_version,
    ))
    assert aligned.quote_status == "matched" and aligned.quote.version == 2
    removed = service.remove_quote(str(actor_id), first.id, RemoveQuoteRequest(expected_state_version=aligned.state_version))
    assert removed.quote is None and removed.quote_status == "no_quote"
    assert service.get_case(str(actor_id), first.id).quote is None
    engine.dispose()


def test_gemini_findings_flow_into_report_with_sources_and_stale_protection():
    from test_gemini_analysis import case_with_media, analyzer_for
    from claim_api.models import AnalysisRunRequest
    from claim_api.analysis import AnalysisService
    from test_analysis import MemoryAnalysisRepository, ACTOR
    case = case_with_media()
    case.scenario_id = 'g1'
    analyzer, _, _ = analyzer_for(case)
    repo = MemoryAnalysisRepository(case)
    updated = AnalysisService(repo, analyzer).run(str(ACTOR), case.id,
        AnalysisRunRequest(expected_state_version=case.state_version)).case
    report = build_report(updated)
    media_lines = [line for line in report.lines if line.id.startswith('analysis.media.')]
    assert media_lines and all(not line.stale for line in media_lines)
    damage = next(line for line in media_lines if line.id == 'analysis.media.damage.0')
    assert '500.00–1000.00 EUR' in damage.text and damage.claim_kind == 'hypothesis'
    assert not any(line.id == 'unknown.visual_damage' for line in report.lines)
    for line in media_lines:
        validate_report_refs(updated, line.source_refs)
    stale = build_report(updated.model_copy(update={'content_revision': updated.content_revision + 1}))
    assert stale.needs_reanalysis and all(line.stale for line in stale.lines if line.id.startswith('analysis.media.'))
    invalid = damage.source_refs[0].model_copy(update={'locator': 'media:video:999'})
    with pytest.raises(ValueError):
        validate_report_refs(updated, [invalid])
