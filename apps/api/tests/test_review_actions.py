from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from psycopg.types.json import Jsonb

from claim_api.case_service import CaseService, StaleCaseError
from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.main import create_app
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.routes.review import get_review_actions_service
from claim_api.review_models import ActionRequest, ApproveDraftRequest, DraftEditRequest
from claim_api.review_service import ReviewActionsService, ReviewTransitionError, draft_digest


def test_verified_recipient_and_gate_set_validation() -> None:
    verified = {"correspondent_name": "Bureau Français Claims Desk", "country": "FR"}
    assert PostgresCaseRepository._recipient_matches_verified(
        {"name": "  Bureau Français  Claims Desk ", "country": "fr"}, verified
    )
    assert not PostgresCaseRepository._recipient_matches_verified(
        {"name": "Invented Payee", "country": "FR"}, verified
    )
    assert not PostgresCaseRepository._recipient_matches_verified(
        {"name": "Bureau Français Claims Desk", "country": "UK"}, verified
    )
    assert PostgresCaseRepository._upstream_gates_pass([
        {"gate": "intake", "status": "passed"},
        {"gate": "counterparty", "status": "passed"},
        {"gate": "evidence", "status": "passed"},
    ])
    assert not PostgresCaseRepository._upstream_gates_pass([
        {"gate": "intake", "status": "passed"},
        {"gate": "counterparty", "status": "passed"},
        {"gate": "evidence", "status": "passed"},
        {"gate": "evidence", "status": "blocked"},
    ])


def _test_database_url() -> str:
    database_url = os.getenv("TEST_MIGRATION_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_MIGRATION_DATABASE_URL to a disposable loopback PostgreSQL test database")
    parsed = make_url(database_url)
    if parsed.host not in {"localhost", "127.0.0.1", "::1"} or "test" not in (parsed.database or "").lower():
        pytest.fail("TEST_MIGRATION_DATABASE_URL must target a loopback database with 'test' in its name")
    return database_url


def _prepare_case(database_url: str, monkeypatch, gates: list[dict[str, object]] | None = None, with_quote: bool = True):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_ALLOW_INSECURE_LOCAL", "true")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", database_url)
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text("create schema if not exists auth"))
        connection.execute(text("create table if not exists auth.users (id uuid primary key)"))
        required_columns = connection.execute(text(
            """select column_name from information_schema.columns
               where table_schema='auth' and table_name='users'
                 and is_nullable='NO' and column_default is null and is_identity='NO'"""
        )).scalars().all()
        if set(required_columns) - {"id"}:
            pytest.skip("review integration requires the minimal local auth.users test stub")
    api_root = Path(__file__).parents[1]
    command.upgrade(Config(str(api_root / "alembic.ini")), "head")
    actor_id = uuid4()
    with engine.begin() as connection:
        connection.execute(text("insert into auth.users (id) values (:id)"), {"id": str(actor_id)})

    repository = PostgresCaseRepository(database_url)
    case = CaseService(repository).create_case(str(actor_id), "complete")
    analysis_id = uuid4()
    draft_id = uuid4()
    recipient = {"name": "Bureau Français Demo Claims Desk (fictional)", "country": "FR"}
    body = "Please review this synthetic claim."
    quote_id = uuid4()
    if with_quote:
        with engine.begin() as connection:
            connection.execute(text("""insert into public.evidence
                (id, case_id, storage_path, kind, source_kind, mode, mime_type, byte_size,
                 client_sha256, checksum_status, original_filename)
                values (:id, :case_id, :path, 'document', 'handler_upload', 'live',
                        'application/pdf', 100, :sha, 'client_declared', 'devis.pdf')"""), {
                "id": str(quote_id), "case_id": str(case.id), "path": f"{case.id}/quote.pdf", "sha": "a" * 64,
            })
            connection.execute(text("""insert into public.case_estimates
                (case_id, version, line_items_json, total_minor, currency, tax_basis,
                 estimate_source, source_refs_json, created_by_user_id)
                values (:case_id, 1, cast(:items as jsonb), 124000, 'EUR', 'TTC',
                        'handler', '[]'::jsonb, :actor_id)"""), {
                "case_id": str(case.id), "items": __import__("json").dumps([
                    {"id": "repair", "label": "Réparation", "amount_minor": 124000}
                ]), "actor_id": str(actor_id),
            })
            connection.execute(text("""insert into public.case_quotes
                (case_id, version, evidence_id, total_ttc_minor, amount_source,
                 attached_estimate_version, created_by_user_id)
                values (:case_id, 1, :pdf_id, 124000, 'handler_entered', 1, :actor_id)"""), {
                "case_id": str(case.id), "pdf_id": str(quote_id), "actor_id": str(actor_id),
            })
    gate_rows = gates if gates is not None else [
        {"gate": "intake", "status": "passed", "reason_codes": [], "source_refs": []},
        {"gate": "counterparty", "status": "passed", "reason_codes": [], "source_refs": []},
        {"gate": "evidence", "status": "passed", "reason_codes": [], "source_refs": []},
    ]
    with engine.begin() as connection:
        connection.execute(text(
            """insert into public.analysis_runs
               (id, case_id, input_content_revision, method_version, status, input_json, output_json, finished_at)
               values (:id, :case_id, :revision, 'claims-analysis-v1', 'ready', '{}', :output, now())"""
        ), {
            "id": str(analysis_id),
            "case_id": str(case.id),
            "revision": case.content_revision,
            "output": __import__("json").dumps({"mode": "live", "output": {}, "gate_results": gate_rows}),
        })
    with repository._connection() as pg_connection, pg_connection.cursor() as cursor:
        cursor.execute("select * from public.cases where id = %s", (case.id,))
        package = repository._package_snapshot(cursor, cursor.fetchone())
    attachments = [quote_id] if with_quote else []
    digest = draft_digest(recipient, 124000 if with_quote else None, "EUR" if with_quote else None, body, attachments, package)
    with engine.begin() as connection:
        connection.execute(text(
            """insert into public.drafts
               (id, case_id, analysis_run_id, version, content_revision, recipient_json,
                amount_minor, currency, body, attachment_ids, sha256, package_json, created_by_user_id)
               values (:id, :case_id, :analysis_id, 1, :revision, :recipient,
                       :amount, :currency, :body, cast(:attachments as jsonb), :sha, cast(:package as jsonb), :actor_id)"""
        ), {
            "id": str(draft_id),
            "case_id": str(case.id),
            "analysis_id": str(analysis_id),
            "revision": case.content_revision,
            "recipient": __import__("json").dumps(recipient),
            "body": body,
            "sha": digest,
            "amount": 124000 if with_quote else None, "currency": "EUR" if with_quote else None,
            "attachments": __import__("json").dumps([str(item) for item in attachments]),
            "package": __import__("json").dumps(package), "actor_id": str(actor_id),
        })
        connection.execute(text(
            "update public.cases set current_draft_id=:draft_id,status='review_ready' where id=:case_id"
        ), {"draft_id": str(draft_id), "case_id": str(case.id)})
    engine.dispose()
    return actor_id, case.id, draft_id, repository


def _refresh_package_after_direct_test_insert(repository: PostgresCaseRepository, case_id: UUID) -> None:
    """Test fixtures insert directly into SQL; keep their draft snapshot in sync."""
    with repository._connection() as connection, connection.cursor() as cursor:
        cursor.execute("select * from public.cases where id = %s", (case_id,))
        case = cursor.fetchone()
        package = repository._package_snapshot(cursor, case)
        cursor.execute("select * from public.drafts where id = %s", (case["current_draft_id"],))
        draft = cursor.fetchone()
        digest = draft_digest(draft["recipient_json"], draft["amount_minor"], draft["currency"],
                              draft["body"], draft["attachment_ids"], package, draft["transmission_comment"])
        cursor.execute("update public.drafts set package_json = %s, sha256 = %s where id = %s",
                       (Jsonb(package), digest, draft["id"]))


def test_review_approval_and_simulated_actions_are_versioned_and_idempotent(monkeypatch) -> None:
    database_url = _test_database_url()
    actor_id, case_id, first_draft_id, repository = _prepare_case(database_url, monkeypatch)
    service = ReviewActionsService(repository)

    initial = CaseService(repository).get_case(str(actor_id), case_id)
    assert initial.gate_results[-1].gate == "approval"
    assert initial.gate_results[-1].status == "needs_review"
    assert initial.current_draft.sha256 == draft_digest(
        initial.current_draft.recipient, initial.current_draft.amount_minor,
        initial.current_draft.currency, initial.current_draft.body, initial.current_draft.attachment_ids,
        initial.current_draft.package, initial.current_draft.transmission_comment,
    )

    with pytest.raises(ReviewTransitionError) as foreign_attachment:
        service.update_draft(
            str(actor_id), case_id,
            DraftEditRequest(attachment_ids=[uuid4()], expected_state_version=1),
        )
    assert foreign_attachment.value.code == "invalid_attachment"
    evidence_id = uuid4()
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text(
            """insert into public.evidence
               (id, case_id, storage_path, kind, source_kind, mode, mime_type, byte_size, checksum_status, client_sha256, sha256_verified)
               values (:id, :case_id, :path, 'scene_photo', 'handler_upload', 'mock', 'image/png', 64, 'verified', :digest, :digest)"""
        ), {"id": str(evidence_id), "case_id": str(case_id), "path": f"{case_id}/review-test.png", "digest": "0" * 64})
    engine.dispose()
    _refresh_package_after_direct_test_insert(repository, case_id)

    edited = service.update_draft(
        str(actor_id), case_id,
        DraftEditRequest(
            body="Revised, human-reviewed synthetic message.",
            attachment_ids=[*initial.current_draft.attachment_ids, evidence_id],
            expected_state_version=1,
        ),
    )
    assert edited.version == 2
    assert edited.parent_draft_id == first_draft_id
    assert edited.content_revision == 2
    assert edited.sha256 != initial.current_draft.sha256
    assert edited.attachment_ids == [*initial.current_draft.attachment_ids, evidence_id]
    with pytest.raises(StaleCaseError):
        service.update_draft(
            str(actor_id), case_id,
            DraftEditRequest(body="stale", expected_state_version=1),
        )

    approved_first_version = service.approve(
        str(actor_id), case_id,
        ApproveDraftRequest(draft_id=edited.id, draft_sha256=edited.sha256, confirmed_review=True, expected_state_version=2),
    )
    approved_view = CaseService(repository).get_case(str(actor_id), case_id)
    assert approved_view.status == "approved"
    assert approved_view.gate_results[-1].status == "passed"
    assert approved_view.approval["draft_sha256"] == edited.sha256

    revised = service.update_draft(
        str(actor_id), case_id,
        DraftEditRequest(body="A second review creates another version.", expected_state_version=3),
    )
    assert revised.version == 3
    invalidated = CaseService(repository).get_case(str(actor_id), case_id)
    assert invalidated.status == "review_ready"
    assert invalidated.approval is None
    assert invalidated.gate_results[-1].status == "needs_review"
    with pytest.raises(ReviewTransitionError) as approval_required:
        service.register(str(actor_id), case_id, ActionRequest(expected_state_version=4), "reg-before-approval")
    assert approval_required.value.code == "approval_required"

    approved = service.approve(
        str(actor_id), case_id,
        ApproveDraftRequest(draft_id=revised.id, draft_sha256=revised.sha256, confirmed_review=True, expected_state_version=4),
    )
    assert approved.draft_sha256 == revised.sha256
    with pytest.raises(ReviewTransitionError) as registration_required:
        service.send(str(actor_id), case_id, ActionRequest(expected_state_version=5), "send-too-soon")
    assert registration_required.value.code == "registration_required"

    registered = service.register(str(actor_id), case_id, ActionRequest(expected_state_version=5), "register-once")
    assert registered.created is True
    assert registered.receipt.reference.startswith("SIM-CLAIM-")
    replay = service.register(str(actor_id), case_id, ActionRequest(expected_state_version=5), "register-once")
    assert replay.created is False and replay.receipt.id == registered.receipt.id
    different_key_repeat = service.register(
        str(actor_id), case_id, ActionRequest(expected_state_version=5), "second-click-key"
    )
    assert different_key_repeat.receipt.id == registered.receipt.id

    sent = service.send(str(actor_id), case_id, ActionRequest(expected_state_version=6), "send-once")
    assert sent.created is True
    assert sent.receipt.registration_action_id == registered.receipt.id
    send_replay = service.send(str(actor_id), case_id, ActionRequest(expected_state_version=6), "send-once")
    assert send_replay.created is False and send_replay.receipt.id == sent.receipt.id

    final_view = CaseService(repository).get_case(str(actor_id), case_id)
    assert final_view.status == "sent"
    assert final_view.state_version == 7
    assert final_view.content_revision == 3
    assert [item["kind"] for item in final_view.actions] == ["registration", "send"]
    assert [event.event_type for event in final_view.timeline][-5:] == [
        "case.draft_approved", "case.draft_edited", "case.draft_approved",
        "case.registration_simulated", "case.send_simulated",
    ]
    with pytest.raises(ReviewTransitionError) as frozen:
        service.update_draft(
            str(actor_id), case_id,
            DraftEditRequest(body="Late edit", expected_state_version=7),
        )
    assert frozen.value.code == "invalid_transition"

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(actor_id))
    app.dependency_overrides[get_review_actions_service] = lambda: service
    client = TestClient(app)
    replay_response = client.post(
        f"/v1/cases/{case_id}/send",
        headers={"Idempotency-Key": "send-once"},
        json={"expected_state_version": 6},
    )
    assert replay_response.status_code == 200
    assert replay_response.json()["id"] == str(sent.receipt.id)
    missing_key = client.post(f"/v1/cases/{case_id}/send", json={"expected_state_version": 7})
    assert missing_key.status_code == 422
    assert missing_key.json()["error"]["code"] == "invalid_request"


def test_approval_fails_closed_when_any_analysis_gate_is_not_passed(monkeypatch) -> None:
    database_url = _test_database_url()
    actor_id, case_id, draft_id, repository = _prepare_case(
        database_url, monkeypatch,
        gates=[
            {"gate": "intake", "status": "passed", "reason_codes": [], "source_refs": []},
            {"gate": "counterparty", "status": "needs_review", "reason_codes": ["ambiguous_match"], "source_refs": []},
            {"gate": "evidence", "status": "passed", "reason_codes": [], "source_refs": []},
        ],
    )
    service = ReviewActionsService(repository)
    with pytest.raises(ReviewTransitionError) as blocked:
        service.approve(
            str(actor_id), case_id,
            ApproveDraftRequest(draft_id=draft_id, draft_sha256=CaseService(repository).get_case(str(actor_id), case_id).current_draft.sha256, confirmed_review=True, expected_state_version=1),
        )
    assert blocked.value.code == "gate_blocked"
    assert blocked.value.details["failed_gates"][0]["reason_codes"] == ["ambiguous_match"]


def test_approval_rejects_unverified_recipient_and_duplicate_analysis_gates(monkeypatch) -> None:
    database_url = _test_database_url()
    actor_id, case_id, draft_id, repository = _prepare_case(database_url, monkeypatch)
    service = ReviewActionsService(repository)

    edited = service.update_draft(
        str(actor_id), case_id,
        DraftEditRequest(
            recipient={"name": "Invented Payee", "country": "FR"},
            expected_state_version=1,
        ),
    )
    with pytest.raises(ReviewTransitionError) as blocked:
        service.approve(
            str(actor_id), case_id,
            ApproveDraftRequest(draft_id=edited.id, draft_sha256=edited.sha256, confirmed_review=True, expected_state_version=2),
        )
    assert blocked.value.code == "gate_blocked"
    assert blocked.value.details["missing_fields"] == ["verified_recipient_match"]

    duplicate_gates = [
        {"gate": "intake", "status": "passed"},
        {"gate": "counterparty", "status": "passed"},
        {"gate": "evidence", "status": "passed"},
        {"gate": "evidence", "status": "blocked"},
    ]
    assert not PostgresCaseRepository._upstream_gates_pass(duplicate_gates)


def test_quote_total_mismatch_blocks_approval_even_when_other_gates_pass(monkeypatch) -> None:
    database_url = _test_database_url()
    actor_id, case_id, _, repository = _prepare_case(database_url, monkeypatch, with_quote=False)
    pdf_id = uuid4()
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text("""insert into public.evidence
            (id, case_id, storage_path, kind, source_kind, mode, mime_type, byte_size,
             client_sha256, checksum_status, original_filename)
            values (:id, :case_id, :path, 'document', 'handler_upload', 'live',
                    'application/pdf', 100, :sha, 'client_declared', 'devis.pdf')"""), {
            "id": str(pdf_id), "case_id": str(case_id),
            "path": f"{case_id}/uploads/{pdf_id}.pdf", "sha": "a" * 64,
        })
        connection.execute(text("""insert into public.case_estimates
            (case_id, version, line_items_json, total_minor, currency, tax_basis,
             estimate_source, source_refs_json, created_by_user_id)
            values (:case_id, 1, cast(:items as jsonb), 124000, 'EUR', 'TTC',
                    'handler', '[]'::jsonb, :actor_id)"""), {
            "case_id": str(case_id), "items": __import__("json").dumps([
                {"id": "repair", "label": "Réparation", "amount_minor": 124000}
            ]), "actor_id": str(actor_id),
        })
        connection.execute(text("""insert into public.case_quotes
            (case_id, version, evidence_id, total_ttc_minor, amount_source,
             attached_estimate_version, created_by_user_id)
            values (:case_id, 1, :pdf_id, 123000, 'handler_entered', 1, :actor_id)"""), {
            "case_id": str(case_id), "pdf_id": str(pdf_id), "actor_id": str(actor_id),
        })
    engine.dispose()
    _refresh_package_after_direct_test_insert(repository, case_id)
    service = ReviewActionsService(repository)
    draft = service.update_draft(str(actor_id), case_id, DraftEditRequest(
        amount_minor=124000, currency="EUR", attachment_ids=[pdf_id], expected_state_version=1,
    ))
    case = CaseService(repository).get_case(str(actor_id), case_id)
    assert case.quote_status == "mismatch"
    assert case.gate_results[-1].reason_codes == ["quote_mismatch"]
    with pytest.raises(ReviewTransitionError) as blocked:
        service.approve(str(actor_id), case_id, ApproveDraftRequest(
            draft_id=draft.id, draft_sha256=draft.sha256, confirmed_review=True, expected_state_version=2,
        ))
    assert blocked.value.code == "gate_blocked"
    assert blocked.value.details == {"quote_total_minor": 123000, "estimate_total_minor": 124000}


def test_confirmation_role_and_missing_quote_fail_without_active_approval(monkeypatch) -> None:
    database_url = _test_database_url()
    actor_id, case_id, _, repository = _prepare_case(database_url, monkeypatch, with_quote=False)
    service = ReviewActionsService(repository)
    draft = service.preview(str(actor_id), case_id)
    with pytest.raises(ReviewTransitionError) as unconfirmed:
        service.approve(str(actor_id), case_id, ApproveDraftRequest(
            draft_id=draft.draft_id, draft_sha256=draft.draft_sha256,
            confirmed_review=False, expected_state_version=1,
        ))
    assert unconfirmed.value.code == "review_confirmation_required"
    with pytest.raises(ReviewTransitionError) as no_quote:
        service.approve(str(actor_id), case_id, ApproveDraftRequest(
            draft_id=draft.draft_id, draft_sha256=draft.draft_sha256,
            confirmed_review=True, expected_state_version=1,
        ))
    assert no_quote.value.details["reason_codes"] == ["estimate_required", "quote_required"]
    assert CaseService(repository).get_case(str(actor_id), case_id).approval is None

    owner, second_case_id, _, repository = _prepare_case(database_url, monkeypatch)
    second = uuid4()
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text("insert into auth.users(id) values (:id)"), {"id": str(second)})
        connection.execute(text("""insert into public.case_memberships
            (case_id, organization_id, user_id, role, can_approve)
            select id, organization_id, :user_id, 'viewer', false from public.cases where id=:case_id"""),
            {"user_id": str(second), "case_id": str(second_case_id)})
    preview = service.preview(str(owner), second_case_id)
    request = ApproveDraftRequest(draft_id=preview.draft_id, draft_sha256=preview.draft_sha256,
                                  confirmed_review=True, expected_state_version=1)
    with pytest.raises(ReviewTransitionError) as forbidden:
        service.approve(str(second), second_case_id, request)
    assert forbidden.value.code == "forbidden_approval"
    assert CaseService(repository).get_case(str(owner), second_case_id).approval is None
    with engine.begin() as connection:
        connection.execute(text("""update public.case_memberships set role='claims_handler', can_approve=true
            where case_id=:case_id and user_id=:user_id"""),
            {"case_id": str(second_case_id), "user_id": str(second)})
    approved = service.approve(str(second), second_case_id, request)
    assert service.approve(str(owner), second_case_id, request).id == approved.id
    engine.dispose()


def test_preview_and_downloaded_receipt_keep_exact_simulated_package(monkeypatch) -> None:
    database_url = _test_database_url()
    actor_id, case_id, _, repository = _prepare_case(database_url, monkeypatch)
    service = ReviewActionsService(repository)
    preview = service.preview(str(actor_id), case_id)
    assert preview.mode == "mock" and "SIMULATION" in preview.simulation
    assert preview.attachments[0]["checksum"] == "a" * 64
    assert preview.package["quote"]["filename"] == "devis.pdf"
    request = ApproveDraftRequest(draft_id=preview.draft_id, draft_sha256=preview.draft_sha256,
                                  confirmed_review=True, expected_state_version=1)
    approval = service.approve(str(actor_id), case_id, request)
    registration = service.register(str(actor_id), case_id, ActionRequest(expected_state_version=2), "reg-receipt")
    registered_preview = service.preview(str(actor_id), case_id)
    assert registered_preview.registration_reference == registration.receipt.reference
    sent = service.send(str(actor_id), case_id, ActionRequest(expected_state_version=3), "send-receipt")
    envelope = service.receipt(str(actor_id), case_id)
    assert envelope == sent.receipt.envelope
    assert envelope["approval_id"] == str(approval.id)
    assert envelope["draft_sha256"] == preview.draft_sha256
    assert envelope["registration_action_id"] == str(registration.receipt.id)
    assert envelope["attachments"] == [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in preview.attachments]
    assert service.send(str(actor_id), case_id, ActionRequest(expected_state_version=3), "send-receipt").receipt.envelope == envelope

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(actor_id))
    app.dependency_overrides[get_review_actions_service] = lambda: service
    response = TestClient(app).get(f"/v1/cases/{case_id}/transmission/receipt")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.json() == envelope


def test_unknown_action_requires_reconciliation(monkeypatch) -> None:
    database_url = _test_database_url()
    actor_id, case_id, _, repository = _prepare_case(database_url, monkeypatch)
    service = ReviewActionsService(repository)
    preview = service.preview(str(actor_id), case_id)
    approval = service.approve(str(actor_id), case_id, ApproveDraftRequest(
        draft_id=preview.draft_id, draft_sha256=preview.draft_sha256,
        confirmed_review=True, expected_state_version=1,
    ))
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as connection:
        connection.execute(text("""insert into public.actions
            (case_id, approval_id, draft_id, kind, mode, status, idempotency_key, payload_hash)
            values (:case_id, :approval_id, :draft_id, 'registration', 'mock', 'unknown', 'unclear', :hash)"""),
            {"case_id": str(case_id), "approval_id": str(approval.id),
             "draft_id": str(preview.draft_id), "hash": "f" * 64})
    with pytest.raises(ReviewTransitionError) as unresolved:
        service.register(str(actor_id), case_id, ActionRequest(expected_state_version=2), "new-key")
    assert unresolved.value.code == "reconciliation_required"
    assert CaseService(repository).get_case(str(actor_id), case_id).status == "approved"
    engine.dispose()
