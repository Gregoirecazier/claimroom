from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from claim_api.case_search import SEARCH_ACCENTS, SEARCH_BASES, normalize_case_search
from claim_api.case_service import CaseReadOnlyError, StaleCaseError, merge_intake
from claim_api.agent_tracing import diagnostic_span
from claim_api.fixtures import ProviderFixture
from claim_api.mock_insurance import SOURCE_VERSION as MOCK_INSURANCE_SOURCE_VERSION
from claim_api.evidence_service import UploadIntentRecord
from claim_api.estimate import G1_DEMO_ITEMS, quote_status
from claim_api.models import (
    AnalysisSourceRef,
    AnalysisGateResult,
    AnalysisOutputV1,
    AttachQuoteRequest,
    AuditEventView,
    CaseListItem,
    CaseListPage,
    CaseView,
    CounterpartyLookupView,
    CameraCandidateView,
    CameraRequestView,
    EvidenceView,
    EstimateItem,
    EstimateView,
    VideoAnalysisLinkView,
    Intake,
    IntakePatch,
    ProviderResultView,
    QuoteView,
    RemoveQuoteRequest,
    ReportLineEditRequest,
    UpsertEstimateRequest,
    VoiceCaseView,
)
from claim_api.mock_actions import ClaimRegistry, ClaimSender, SimulatedClaimRegistry, SimulatedClaimSender
from claim_api.review_models import ActionRequest, ActionReceiptView, ActionResult, ApproveDraftRequest, ApprovalView, DraftEditRequest, DraftView, TransmissionPreviewView
from claim_api.review_service import ReviewTransitionError, canonical_recipient, draft_digest
from claim_api.report import build_report, validate_report_refs


class DatabaseUnavailableError(RuntimeError):
    pass


class DatabaseConfigurationError(ValueError):
    pass


ANALYSIS_ORPHAN_TIMEOUT = timedelta(minutes=5)


def validate_database_url(database_url: str, environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else dict(os.environ)
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise DatabaseConfigurationError("DATABASE_URL must be a PostgreSQL URI.")

    parameters = parse_qsl(parsed.query, keep_blank_values=True)
    ssl_modes = [value.lower() for key, value in parameters if key.lower() == "sslmode"]
    ssl_mode = ssl_modes[-1] if ssl_modes else ""
    if ssl_mode in {"require", "verify-ca", "verify-full"}:
        return database_url

    local_auth = env.get("APP_ENV", "production").lower() == "development"
    local_flag = env.get("DATABASE_ALLOW_INSECURE_LOCAL", "false").strip().lower() in {"1", "true", "yes", "on"}
    is_vercel = env.get("VERCEL", "false").strip().lower() in {"1", "true", "yes", "on"}
    is_loopback = parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    if not (local_auth and local_flag and not is_vercel and is_loopback):
        raise DatabaseConfigurationError(
            "PostgreSQL TLS is required; local plaintext needs an explicit development-only opt-in."
        )

    parameters = [(key, value) for key, value in parameters if key.lower() != "sslmode"]
    parameters.append(("sslmode", "disable"))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(parameters), parsed.fragment))


class PostgresCaseRepository:
    """Small transaction-per-request adapter for Supabase Postgres."""

    def __init__(
        self,
        database_url: str,
        claim_registry: ClaimRegistry | None = None,
        claim_sender: ClaimSender | None = None,
    ) -> None:
        self.database_url = validate_database_url(database_url)
        self.claim_registry = claim_registry or SimulatedClaimRegistry()
        self.claim_sender = claim_sender or SimulatedClaimSender()

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        try:
            with diagnostic_span("db.connect"):
                connection = psycopg.connect(
                    self.database_url,
                    row_factory=dict_row,
                    connect_timeout=5,
                    prepare_threshold=None,
                )
            with connection:
                yield connection
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            raise DatabaseUnavailableError("Database connection failed.") from exc

    def create_case(
        self,
        actor_id: UUID,
        scenario_id: str,
        intake: Intake,
        provider_results: tuple[ProviderFixture, ...],
        scenario_version: str,
        provider_source_version: str,
    ) -> CaseView:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.cases
                    (created_by_user_id, scenario_id, synthetic, status, state_version,
                     content_revision, intake_json)
                values (%s, %s, true, 'collecting', 1, 1, %s)
                returning *
                """,
                (actor_id, scenario_id, Jsonb(intake.model_dump(mode="json"))),
            )
            case_row = cursor.fetchone()
            cursor.execute(
                """insert into public.case_memberships
                   (case_id, organization_id, user_id, role, can_approve)
                   values (%s, %s, %s, 'claims_handler', true)""",
                (case_row["id"], case_row["organization_id"], actor_id),
            )
            provider_ids: list[str] = []
            for fixture in provider_results:
                query_hash = hashlib.sha256(
                    json.dumps(fixture.query, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                cursor.execute(
                    """
                insert into public.provider_results
                    (case_id, provider, mode, status, query_hash, source_version, query_json, payload_json, reason)
                values (%s, %s, 'mock', %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        case_row["id"],
                        fixture.provider,
                        fixture.status,
                        query_hash,
                        provider_source_version,
                        Jsonb(fixture.query),
                        Jsonb(fixture.data),
                        fixture.reason,
                    ),
                )
                provider_ids.append(str(cursor.fetchone()["id"]))

            estimate_id = None
            if scenario_id == "g1":
                cursor.execute(
                    """insert into public.case_estimates
                       (case_id, version, line_items_json, total_minor, currency, tax_basis,
                        estimate_source, source_refs_json, created_by_user_id)
                       values (%s, 1, %s, %s, 'EUR', 'TTC', 'demo_fixture', '[]'::jsonb, null)
                       returning id""",
                    (case_row["id"], Jsonb([item.model_dump() for item in G1_DEMO_ITEMS]),
                     sum(item.amount_minor for item in G1_DEMO_ITEMS)),
                )
                estimate_id = str(cursor.fetchone()["id"])

            cursor.execute(
                """
                insert into public.audit_events
                    (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                     content_revision_before, content_revision_after, metadata_json)
                values (%s, %s, 'case.created', 0, 1, 0, 1, %s)
                """,
                (
                    case_row["id"],
                    actor_id,
                    Jsonb({
                        "scenario_id": scenario_id,
                        "fixture_version": scenario_version,
                        "provider_source_version": provider_source_version,
                        "provider_result_ids": provider_ids,
                        "demo_estimate_id": estimate_id,
                    }),
                ),
            )
            return self._case_view(cursor, case_row)

    def delete_case(self, actor_id: UUID, case_id: UUID) -> bool:
        """Delete a case and its private relational data in one transaction.

        Shared media analysis artifacts and Storage objects are retained. No new
        signed URLs can be issued once the owning case/evidence rows are gone.
        """
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select id from public.cases where id = %s and created_by_user_id = %s for update",
                (case_id, actor_id),
            )
            if cursor.fetchone() is None:
                return False

            # Break the current-draft cycle and self-references before deleting
            # dependent rows. The case lock serializes this with case edits.
            cursor.execute("update public.cases set current_draft_id = null where id = %s", (case_id,))
            cursor.execute("update public.actions set registration_action_id = null where case_id = %s", (case_id,))
            cursor.execute("update public.drafts set parent_draft_id = null where case_id = %s", (case_id,))
            cursor.execute(
                "delete from public.voice_session_events where session_id in "
                "(select id from public.voice_sessions where case_id = %s)", (case_id,),
            )
            for table in (
                "insured_corrections", "deposit_demo_media", "evidence_upload_intents",
                "case_portal_messages", "deposit_chat_history", "garage_reply_jobs", "garage_sms_settings",
            ):
                cursor.execute(sql.SQL("delete from public.{} where case_id = %s").format(sql.Identifier(table)), (case_id,))
            cursor.execute(
                "delete from public.deposit_sessions where grant_id in "
                "(select id from public.deposit_grants where case_id = %s)", (case_id,),
            )
            # Leaves first: foreign keys deliberately use ON DELETE RESTRICT.
            for table in (
                "deposit_grants", "case_messages", "case_whatsapp_inbound",
                "voice_sessions", "case_media_analysis_links", "case_camera_requests",
                "case_quotes", "case_estimates", "report_line_edits",
                "case_counterparty_lookups", "actions", "approvals", "drafts",
                "case_media_workflows", "case_correspondence", "dust_runs",
                "analysis_runs", "provider_results", "evidence", "audit_events",
                "case_memberships",
            ):
                cursor.execute(sql.SQL("delete from public.{} where case_id = %s").format(sql.Identifier(table)), (case_id,))
            cursor.execute("delete from public.cases where id = %s and created_by_user_id = %s", (case_id, actor_id))
            return True

    def list_cases(self, actor_id: UUID, limit: int) -> list[CaseListItem]:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select * from public.cases
                where created_by_user_id = %s
                order by updated_at desc, id desc
                limit %s
                """,
                (actor_id, limit),
            )
            return [self._summary(row) for row in cursor.fetchall()]

    def list_cases_page(self, actor_id: UUID, query: str, offset: int, limit: int) -> CaseListPage:
        # Parameterized literal substring search, with the same plate/name
        # normalization as the UI; % and _ are never SQL wildcards.
        predicate = """created_by_user_id = %s and (%s = '' or strpos(
            regexp_replace(translate(lower(concat_ws(' ',
                id::text, intake_json->>'insured_reference', intake_json->>'insured_name',
                intake_json->>'insured_plate', intake_json->>'insured_vehicle',
                intake_json->>'location')), %s, %s), '[[:space:]-]+', '', 'g'), %s) > 0)"""
        normalized = normalize_case_search(query)
        params = (actor_id, normalized, SEARCH_ACCENTS, SEARCH_BASES, normalized)
        with self._connection() as connection, connection.cursor() as cursor:
            # Both rows and total use the same snapshot, including empty pages.
            cursor.execute("set transaction isolation level repeatable read")
            cursor.execute("select count(*) as total from public.cases where " + predicate, params)
            total = cursor.fetchone()["total"]
            cursor.execute("select * from public.cases where " + predicate +
                           " order by updated_at desc, id desc limit %s offset %s", (*params, limit, offset))
            items = [self._summary(row) for row in cursor.fetchall()]
            return CaseListPage(items=items, total=total, offset=offset, limit=limit,
                                has_more=offset + len(items) < total)

    def get_case(self, actor_id: UUID, case_id: UUID) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from public.cases where id = %s and created_by_user_id = %s",
                (case_id, actor_id),
            )
            row = cursor.fetchone()
            return None if row is None else self._case_view(cursor, row)

    def create_camera_request(
        self, actor_id: UUID, case_id: UUID, candidate: CameraCandidateView,
        scope: str, reason: str, expected_state_version: int,
    ) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            current = self._lock_owned_case(cursor, actor_id, case_id)
            if current is None:
                return None
            cursor.execute(
                "select * from public.case_camera_requests where case_id=%s and candidate_id=%s",
                (case_id, candidate.id),
            )
            prior = cursor.fetchone()
            if prior is not None:
                if prior["scope"] != scope or prior["reason"] != reason:
                    raise ValueError("A different request already exists for this camera candidate.")
                return self._case_view(cursor, current)
            self._check_material_edit(current, expected_state_version)
            cursor.execute("""insert into public.case_camera_requests
                (case_id, candidate_id, candidate_label, source, controller, recipient,
                 scope, reason, fixture_event_id, created_by_user_id)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (case_id, candidate.id, candidate.label, candidate.source,
                 candidate.controller, candidate.recipient, scope, reason,
                 candidate.fixture_event_id, actor_id),
            )
            request_id = cursor.fetchone()["id"]
            updated = self._record_camera_transition(
                cursor, current, actor_id, "case.camera_request_drafted",
                {"camera_request_id": str(request_id), "candidate_id": candidate.id},
            )
            return self._case_view(cursor, updated)

    def change_camera_request(
        self, actor_id: UUID, case_id: UUID, request_id: UUID,
        expected_state_version: int, status: str,
    ) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            current = self._lock_owned_case(cursor, actor_id, case_id)
            if current is None:
                return None
            cursor.execute(
                "select * from public.case_camera_requests where case_id=%s and id=%s for update",
                (case_id, request_id),
            )
            prior = cursor.fetchone()
            if prior is None:
                raise ValueError("Camera request does not belong to this case.")
            if prior["status"] == status:
                return self._case_view(cursor, current)
            self._check_material_edit(current, expected_state_version)
            if status == "requested":
                if prior["status"] != "draft" or not prior["controller"] or not prior["recipient"]:
                    raise ValueError("A draft with a known controller and recipient is required for approval.")
                cursor.execute("""update public.case_camera_requests set status='requested',
                    approved_by_user_id=%s, status_actor_user_id=%s,
                    approved_at=now(), requested_at=now(), status_changed_at=now()
                    where id=%s""", (actor_id, actor_id, request_id))
            else:
                allowed = {
                    "requested": {"waiting", "denied", "unknown_owner", "unavailable", "timed_out"},
                    "waiting": {"denied", "unknown_owner", "unavailable", "timed_out"},
                }
                if status not in allowed.get(prior["status"], set()):
                    raise ValueError("This CCTV request status transition is not allowed.")
                cursor.execute("""update public.case_camera_requests
                    set status=%s, status_actor_user_id=%s, status_changed_at=now()
                    where id=%s""", (status, actor_id, request_id))
            updated = self._record_camera_transition(
                cursor, current, actor_id, "case.camera_request_status_changed",
                {"camera_request_id": str(request_id), "from": prior["status"], "to": status,
                 "external_contact": False},
            )
            return self._case_view(cursor, updated)

    def _record_camera_transition(
        self, cursor: psycopg.Cursor[dict[str, Any]], current: dict[str, Any],
        actor_id: UUID, event_type: str, metadata: dict[str, Any],
    ) -> dict[str, Any]:
        cursor.execute("""update public.cases
            set state_version=state_version+1, updated_at=now()
            where id=%s and state_version=%s returning *""",
            (current["id"], current["state_version"]),
        )
        updated = cursor.fetchone()
        if updated is None:
            raise StaleCaseError(current["state_version"] + 1)
        self._insert_workflow_audit(cursor, current["id"], actor_id, event_type,
                                    current, updated, metadata)
        return updated

    @staticmethod
    def _latest_estimate_row(cursor: psycopg.Cursor[dict[str, Any]], case_id: UUID) -> dict[str, Any] | None:
        cursor.execute("select * from public.case_estimates where case_id = %s order by version desc limit 1", (case_id,))
        return cursor.fetchone()

    @staticmethod
    def _latest_quote_row(cursor: psycopg.Cursor[dict[str, Any]], case_id: UUID) -> dict[str, Any] | None:
        cursor.execute("select * from public.case_quotes where case_id = %s order by version desc limit 1", (case_id,))
        return cursor.fetchone()

    def _record_material_change(
        self, cursor: psycopg.Cursor[dict[str, Any]], current: dict[str, Any], actor_id: UUID,
        event_type: str, metadata: dict[str, Any],
    ) -> dict[str, Any]:
        cursor.execute(
            """update public.cases set state_version = state_version + 1,
               content_revision = content_revision + 1, status = 'collecting',
               current_draft_id = null, updated_at = now()
               where id = %s and state_version = %s returning *""",
            (current["id"], current["state_version"]),
        )
        updated = cursor.fetchone()
        if updated is None:
            raise StaleCaseError(current["state_version"] + 1)
        cursor.execute(
            "update public.approvals set superseded_at = now() where case_id = %s and superseded_at is null",
            (current["id"],),
        )
        self._insert_workflow_audit(cursor, current["id"], actor_id, event_type, current, updated, metadata)
        return updated

    @staticmethod
    def _check_material_edit(current: dict[str, Any], expected_state_version: int) -> None:
        if current["state_version"] != expected_state_version:
            raise StaleCaseError(current["state_version"])
        if current["status"] in {"registered", "sent"}:
            raise ValueError("A registered or sent case is read-only.")

    def edit_report_line(
        self, actor_id: UUID, case_id: UUID, line_id: str, request: ReportLineEditRequest,
    ) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            current = self._lock_owned_case(cursor, actor_id, case_id)
            if current is None:
                return None
            self._check_material_edit(current, request.expected_state_version)
            view = self._case_view(cursor, current)
            line = next((item for item in view.report.lines if item.id == line_id), None)
            if line is None or line.stale:
                raise ValueError("The report line is missing or stale; refresh and rerun analysis first.")
            if not line.source_refs:
                raise ValueError("An unresolved fact cannot be corrected without an existing source.")
            validate_report_refs(view, line.source_refs)
            if line.text == request.text and line.uncertainty == request.uncertainty:
                return view
            next_revision = current["content_revision"] + 1
            cursor.execute(
                """insert into public.report_line_edits
                   (case_id, line_id, text, uncertainty, source_refs_json, previous_text,
                    as_of_revision, actor_user_id)
                   values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                (case_id, line_id, request.text, request.uncertainty,
                 Jsonb([ref.model_dump() for ref in line.source_refs]), line.text,
                 next_revision, actor_id),
            )
            edit_id = cursor.fetchone()["id"]
            updated = self._record_material_change(cursor, current, actor_id, "case.report_line_edited", {
                "edit_id": str(edit_id), "line_id": line_id,
                "changed_fields": ["text", "uncertainty"],
            })
            return self._case_view(cursor, updated)

    def upsert_estimate(
        self, actor_id: UUID, case_id: UUID, request: UpsertEstimateRequest,
    ) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            current = self._lock_owned_case(cursor, actor_id, case_id)
            if current is None:
                return None
            self._check_material_edit(current, request.expected_state_version)
            if request.source_refs:
                validate_report_refs(self._case_view(cursor, current), request.source_refs)
            prior = self._latest_estimate_row(cursor, case_id)
            items = [item.model_dump() for item in request.line_items]
            refs = [ref.model_dump() for ref in request.source_refs]
            if (prior and prior["line_items_json"] == items and prior["total_minor"] == request.total_minor
                    and prior["estimate_source"] == request.estimate_source and prior["source_refs_json"] == refs):
                return self._case_view(cursor, current)
            version = (prior["version"] if prior else 0) + 1
            cursor.execute(
                """insert into public.case_estimates
                   (case_id, version, line_items_json, total_minor, currency, tax_basis,
                    estimate_source, source_refs_json, created_by_user_id)
                   values (%s, %s, %s, %s, 'EUR', 'TTC', %s, %s, %s) returning id""",
                (case_id, version, Jsonb(items), request.total_minor, request.estimate_source,
                 Jsonb(refs), actor_id),
            )
            estimate_id = cursor.fetchone()["id"]
            updated = self._record_material_change(cursor, current, actor_id, "case.estimate_updated", {
                "estimate_id": str(estimate_id), "version": version,
                "previous_total_minor": prior["total_minor"] if prior else None,
                "total_minor": request.total_minor, "estimate_source": request.estimate_source,
            })
            return self._case_view(cursor, updated)

    def attach_quote(
        self, actor_id: UUID, case_id: UUID, request: AttachQuoteRequest,
    ) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            current = self._lock_owned_case(cursor, actor_id, case_id)
            if current is None:
                return None
            self._check_material_edit(current, request.expected_state_version)
            cursor.execute(
                "select * from public.evidence where case_id = %s and id = %s",
                (case_id, request.evidence_id),
            )
            evidence = cursor.fetchone()
            if (evidence is None or evidence["mime_type"] != "application/pdf"
                    or evidence["kind"] != "document" or not evidence["client_sha256"]):
                raise ValueError("Choose a finalized PDF document belonging to this case.")
            estimate = self._latest_estimate_row(cursor, case_id)
            prior = self._latest_quote_row(cursor, case_id)
            estimate_version = estimate["version"] if estimate else None
            if (prior and prior["evidence_id"] == request.evidence_id
                    and prior["total_ttc_minor"] == request.total_ttc_minor
                    and prior["attached_estimate_version"] == estimate_version):
                return self._case_view(cursor, current)
            version = (prior["version"] if prior else 0) + 1
            cursor.execute(
                """insert into public.case_quotes
                   (case_id, version, evidence_id, total_ttc_minor, amount_source,
                    attached_estimate_version, created_by_user_id)
                   values (%s, %s, %s, %s, 'handler_entered', %s, %s) returning id""",
                (case_id, version, request.evidence_id, request.total_ttc_minor,
                 estimate_version, actor_id),
            )
            quote_id = cursor.fetchone()["id"]
            updated = self._record_material_change(cursor, current, actor_id, "case.quote_attached", {
                "quote_id": str(quote_id), "version": version,
                "evidence_id": str(request.evidence_id),
                "previous_evidence_id": str(prior["evidence_id"]) if prior and prior["evidence_id"] else None,
                "total_ttc_minor": request.total_ttc_minor, "amount_source": "handler_entered",
            })
            return self._case_view(cursor, updated)

    def remove_quote(
        self, actor_id: UUID, case_id: UUID, request: RemoveQuoteRequest,
    ) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            current = self._lock_owned_case(cursor, actor_id, case_id)
            if current is None:
                return None
            self._check_material_edit(current, request.expected_state_version)
            prior = self._latest_quote_row(cursor, case_id)
            if prior is None or prior["evidence_id"] is None:
                return self._case_view(cursor, current)
            cursor.execute(
                """insert into public.case_quotes (case_id, version, created_by_user_id)
                   values (%s, %s, %s) returning id""",
                (case_id, prior["version"] + 1, actor_id),
            )
            removal_id = cursor.fetchone()["id"]
            updated = self._record_material_change(cursor, current, actor_id, "case.quote_removed", {
                "quote_removal_id": str(removal_id),
                "previous_evidence_id": str(prior["evidence_id"]),
            })
            return self._case_view(cursor, updated)

    def begin_analysis(
        self, actor_id: UUID, case_id: UUID, expected_state_version: int,
        run_id: UUID, method_version: str, mode: str, input_json: dict[str, Any],
    ) -> int:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select state_version, content_revision from public.cases where id = %s and created_by_user_id = %s for update",
                (case_id, actor_id),
            )
            case_row = cursor.fetchone()
            if case_row is None:
                return -1
            if case_row["state_version"] != expected_state_version:
                raise StaleCaseError(case_row["state_version"])
            cursor.execute(
                "select triage_json from public.voice_sessions where case_id = %s",
                (case_id,),
            )
            voice_row = cursor.fetchone()
            if voice_row and voice_row["triage_json"].get("status") == "urgent_human_handoff":
                from claim_api.analysis import UrgentHandoffError
                raise UrgentHandoffError("Urgent voice intake requires human handling before analysis.")
            cursor.execute(
                """select id, started_at, method_version, output_json from public.analysis_runs
                   where case_id = %s and status = 'running' for update""",
                (case_id,),
            )
            running_rows = cursor.fetchall()
            cutoff = datetime.now(timezone.utc) - ANALYSIS_ORPHAN_TIMEOUT
            if any(row["started_at"] >= cutoff for row in running_rows):
                from claim_api.analysis import AnalysisInProgressError
                raise AnalysisInProgressError("An analysis is already running for this case.")
            for orphan in running_rows:
                envelope = orphan["output_json"] or {}
                mode = envelope.get("mode", "live")
                envelope.update({
                    "mode": mode if mode in {"mock", "live"} else "live",
                    "output": None,
                    "gate_results": [],
                    "error_message": "The prior analysis did not finish and was recovered. Retry the analysis.",
                })
                cursor.execute(
                    """update public.analysis_runs
                       set status = 'failed', error_code = 'analysis_timeout_recovered',
                           output_json = %s, finished_at = now()
                       where id = %s and status = 'running'""",
                    (Jsonb(envelope), orphan["id"]),
                )
                cursor.execute(
                    """insert into public.audit_events
                       (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                        content_revision_before, content_revision_after, metadata_json)
                       values (%s, %s, 'case.analysis_timeout_recovered', %s, %s, %s, %s, %s)""",
                    (case_id, actor_id, case_row["state_version"], case_row["state_version"],
                     case_row["content_revision"], case_row["content_revision"],
                     Jsonb({
                         "analysis_run_id": str(orphan["id"]),
                         "method_version": orphan["method_version"],
                         "mode": mode if mode in {"mock", "live"} else "live",
                         "reason_code": "analysis_timeout_recovered",
                     })),
                )
            cursor.execute(
                """insert into public.analysis_runs
                   (id, case_id, input_content_revision, method_version, status, input_json, output_json)
                   values (%s, %s, %s, %s, 'running', %s, %s)""",
                (run_id, case_id, case_row["content_revision"], method_version, Jsonb(input_json), Jsonb({"mode": mode})),
            )
            return case_row["content_revision"]

    def complete_analysis(
        self, actor_id: UUID, case_id: UUID, run_id: UUID, input_content_revision: int,
        *, status: str, mode: str, output: AnalysisOutputV1 | None,
        gates: list[AnalysisGateResult], error_code: str | None,
        error_message: str | None, draft: dict[str, Any] | None,
    ) -> CaseView | None:
        if status not in {"ready", "failed"} or mode not in {"mock", "live"}:
            raise ValueError("Invalid analysis completion status or mode.")
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from public.cases where id = %s and created_by_user_id = %s for update",
                (case_id, actor_id),
            )
            case_row = cursor.fetchone()
            if case_row is None:
                return None
            cursor.execute(
                "select * from public.analysis_runs where id = %s and case_id = %s for update",
                (run_id, case_id),
            )
            run_row = cursor.fetchone()
            if run_row is None or run_row["status"] != "running":
                raise ValueError("Analysis run is missing or already finished.")

            stale = case_row["content_revision"] != input_content_revision
            run_status = "stale" if stale else status
            envelope = {
                "mode": mode,
                "output": output.model_dump(mode="json") if output and not stale else None,
                "gate_results": [item.model_dump(mode="json") for item in gates] if not stale else [],
                "error_message": error_message if not stale else "The case changed while analysis was running. Refresh and run analysis again.",
            }
            cursor.execute(
                """update public.analysis_runs
                   set status = %s, output_json = %s, error_code = %s, finished_at = now()
                   where id = %s""",
                (run_status, Jsonb(envelope), "stale_input_revision" if stale else error_code, run_id),
            )

            current_draft_id = case_row["current_draft_id"]
            event_type = "case.analysis_stale" if stale else "case.analysis_failed" if status == "failed" else "case.analysis_completed"
            if not stale and status == "ready" and draft is not None:
                package = self._package_snapshot(cursor, case_row)
                quote_row = self._latest_quote_row(cursor, case_id)
                quote_attachment_ids = [quote_row["evidence_id"]] if quote_row and quote_row["evidence_id"] else []
                cursor.execute(
                    "select coalesce(max(version), 0) as version from public.drafts where case_id = %s",
                    (case_id,),
                )
                version = cursor.fetchone()["version"] + 1
                parent_draft_id = current_draft_id
                draft_revision = case_row["content_revision"] + 1
                draft_hash = draft_digest(
                    draft.get("recipient"), draft.get("amount_minor"), draft.get("currency"),
                    draft["body"], quote_attachment_ids, package,
                )
                cursor.execute(
                    """insert into public.drafts
                       (case_id, analysis_run_id, parent_draft_id, version, content_revision,
                        recipient_json, amount_minor, currency, body, attachment_ids, sha256,
                        package_json, created_by_user_id)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       returning *""",
                    (case_id, run_id, parent_draft_id, version, draft_revision,
                     Jsonb(draft["recipient"]) if draft.get("recipient") else None,
                     draft.get("amount_minor"), draft.get("currency"), draft["body"],
                     Jsonb([str(item) for item in quote_attachment_ids]), draft_hash,
                     Jsonb(package), actor_id),
                )
                current_draft_id = cursor.fetchone()["id"]
                cursor.execute(
                    """update public.cases set current_draft_id = %s, status = 'review_ready',
                       content_revision = content_revision + 1, state_version = state_version + 1,
                       updated_at = now() where id = %s returning *""",
                    (current_draft_id, case_id),
                )
            else:
                cursor.execute(
                    """update public.cases set state_version = state_version + 1,
                       updated_at = now() where id = %s returning *""",
                    (case_id,),
                )
            updated_case = cursor.fetchone()
            cursor.execute(
                """insert into public.audit_events
                   (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                    content_revision_before, content_revision_after, metadata_json)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (case_id, actor_id, event_type, case_row["state_version"], updated_case["state_version"],
                 case_row["content_revision"], updated_case["content_revision"],
                 Jsonb({"analysis_run_id": str(run_id), "method_version": run_row["method_version"],
                        "mode": mode, "status": run_status, "draft_created": current_draft_id != case_row["current_draft_id"]})),
            )
            return self._case_view(cursor, updated_case)

    def update_intake(
        self,
        actor_id: UUID,
        case_id: UUID,
        expected_state_version: int,
        patch: IntakePatch,
    ) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select * from public.cases
                where id = %s and created_by_user_id = %s
                for update
                """,
                (case_id, actor_id),
            )
            current = cursor.fetchone()
            if current is None:
                return None
            if current["state_version"] != expected_state_version:
                raise StaleCaseError(current["state_version"])

            old_intake = Intake.model_validate(current["intake_json"])
            updated_intake = merge_intake(old_intake, patch)
            if updated_intake.model_dump(mode="json") == old_intake.model_dump(mode="json"):
                return self._case_view(cursor, current)
            changed_fields = list(patch.model_dump(exclude_unset=True))
            if (old_intake.incident_at.date() if old_intake.incident_at else None) != (updated_intake.incident_at.date() if updated_intake.incident_at else None):
                cursor.execute("update public.case_counterparty_lookups set active=false,updated_at=now() where case_id=%s", (case_id,))
            cursor.execute(
                """
                update public.cases
                set intake_json = %s,
                    state_version = state_version + 1,
                    content_revision = content_revision + 1,
                    status = 'collecting',
                    current_draft_id = null,
                    updated_at = now()
                where id = %s and created_by_user_id = %s and state_version = %s
                returning *
                """,
                (
                    Jsonb(updated_intake.model_dump(mode="json")),
                    case_id,
                    actor_id,
                    expected_state_version,
                ),
            )
            updated = cursor.fetchone()
            if updated is None:
                cursor.execute(
                    "select state_version from public.cases where id = %s and created_by_user_id = %s",
                    (case_id, actor_id),
                )
                current_version = cursor.fetchone()
                if current_version is None:
                    return None
                raise StaleCaseError(current_version["state_version"])

            cursor.execute(
                "update public.approvals set superseded_at = now() where case_id = %s and superseded_at is null",
                (case_id,),
            )
            cursor.execute(
                """
                insert into public.audit_events
                    (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                     content_revision_before, content_revision_after, metadata_json)
                values (%s, %s, 'case.intake_updated', %s, %s, %s, %s, %s)
                """,
                (
                    case_id,
                    actor_id,
                    current["state_version"],
                    updated["state_version"],
                    current["content_revision"],
                    updated["content_revision"],
                    Jsonb({"changed_fields": changed_fields}),
                ),
            )
            # Corrected intake invalidates the old assessment. Resume analysis for
            # cases with visual evidence, in the same transaction as the edit.
            cursor.execute("select 1 from public.evidence where case_id=%s and "
                           "(mime_type like 'image/%%' or mime_type like 'video/%%') limit 1", (case_id,))
            if cursor.fetchone():
                from claim_api.media_workflow import enqueue_media
                enqueue_media(cursor, case_id, updated["content_revision"])
            return self._case_view(cursor, updated)

    def case_state(self, actor_id: UUID, case_id: UUID) -> tuple[int, str] | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select state_version, scenario_id from public.cases where id = %s and created_by_user_id = %s",
                (case_id, actor_id),
            )
            row = cursor.fetchone()
            return None if row is None else (row["state_version"], row["scenario_id"])

    @staticmethod
    def _lock_insured_upload_grant(cursor: psycopg.Cursor, grant_id: UUID | None, case_id: UUID) -> None:
        # The caller already holds the case lock. Keep that lock order aligned
        # with DepositGrantService.revoke and hold the grant lock until commit.
        from claim_api.deposit_grants import DepositGrantError

        if grant_id is None:
            raise DepositGrantError("invalid_grant")
        cursor.execute(
            """select g.revoked_at, g.expires_at, g.capabilities, g.message_id,
                      m.case_id as message_case_id
               from public.deposit_grants g
               left join public.case_messages m on m.id = g.message_id
               where g.id = %s and g.case_id = %s for share of g""",
            (grant_id, case_id),
        )
        grant = cursor.fetchone()
        if grant is None or grant["message_id"] is None or grant["message_case_id"] != case_id:
            raise DepositGrantError("invalid_grant")
        if grant["revoked_at"] is not None:
            raise DepositGrantError("revoked_grant")
        if grant["expires_at"] <= datetime.now(timezone.utc):
            raise DepositGrantError("expired_grant")
        if "upload_evidence" not in grant["capabilities"]:
            raise DepositGrantError("forbidden_capability")

    def register_upload_intent(
        self,
        actor_id: UUID,
        case_id: UUID,
        expected_state_version: int,
        storage_path: str,
        kind: str,
        mime_type: str,
        byte_size: int,
        client_sha256: str,
        expires_at: datetime,
        filename: str | None = None,
        source_kind: str = "handler_upload",
        deposit_grant_id: UUID | None = None,
    ) -> bool:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select state_version, content_revision, status from public.cases where id = %s and created_by_user_id = %s for update",
                (case_id, actor_id),
            )
            case_row = cursor.fetchone()
            if case_row is None:
                return False
            if source_kind == "insured_upload" and case_row["status"] in {"registered", "sent"}:
                raise CaseReadOnlyError
            if case_row["state_version"] != expected_state_version:
                raise StaleCaseError(case_row["state_version"])
            if source_kind == "insured_upload":
                self._lock_insured_upload_grant(cursor, deposit_grant_id, case_id)
            cursor.execute(
                """insert into public.evidence_upload_intents
                   (case_id, actor_user_id, storage_path, kind, mime_type, byte_size,
                    client_sha256, expires_at, filename, source_kind, deposit_grant_id)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                (case_id, actor_id, storage_path, kind, mime_type, byte_size,
                 client_sha256, expires_at, filename, source_kind, deposit_grant_id),
            )
            intent_id = cursor.fetchone()["id"]
            if source_kind == "insured_upload":
                cursor.execute("""insert into public.audit_events
                    (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                     content_revision_before, content_revision_after, metadata_json)
                    values (%s,null,'deposit.upload_intent_issued',%s,%s,%s,%s,%s)""",
                    (case_id, case_row["state_version"], case_row["state_version"],
                     case_row["content_revision"], case_row["content_revision"],
                     Jsonb({"intent_id": str(intent_id), "grant_id": str(deposit_grant_id), "kind": kind})))
            return True

    def get_upload_intent(
        self, actor_id: UUID, case_id: UUID, storage_path: str
    ) -> UploadIntentRecord | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """select i.* from public.evidence_upload_intents i
                   join public.cases c on c.id = i.case_id
                   where i.actor_user_id = %s and i.case_id = %s and i.storage_path = %s
                     and c.created_by_user_id = %s""",
                (actor_id, case_id, storage_path, actor_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return UploadIntentRecord(
                id=row["id"],
                case_id=row["case_id"],
                created_by_user_id=row["actor_user_id"],
                storage_path=row["storage_path"],
                kind=row["kind"],
                mime_type=row["mime_type"],
                byte_size=row["byte_size"],
                client_sha256=row["client_sha256"],
                expires_at=row["expires_at"],
                finalized_evidence_id=row["finalized_evidence_id"],
                deposit_grant_id=row["deposit_grant_id"],
            )

    def finalize_evidence(
        self,
        actor_id: UUID,
        case_id: UUID,
        storage_path: str,
        expected_state_version: int,
        sha256_verified: str | None = None,
    ) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from public.cases where id = %s and created_by_user_id = %s for update",
                (case_id, actor_id),
            )
            current = cursor.fetchone()
            if current is None:
                return None
            cursor.execute(
                """select * from public.evidence_upload_intents
                   where actor_user_id = %s and case_id = %s and storage_path = %s for update""",
                (actor_id, case_id, storage_path),
            )
            intent = cursor.fetchone()
            if intent is None:
                return None
            if intent["source_kind"] == "insured_upload" and current["status"] in {"registered", "sent"}:
                raise CaseReadOnlyError
            if intent["source_kind"] == "insured_upload":
                self._lock_insured_upload_grant(cursor, intent["deposit_grant_id"], case_id)
            if intent["finalized_evidence_id"] is not None:
                return self._case_view(cursor, current)
            if current["state_version"] != expected_state_version:
                raise StaleCaseError(current["state_version"])
            if intent["expires_at"] <= datetime.now(timezone.utc):
                return None

            evidence_id = uuid4()
            cursor.execute(
                """insert into public.evidence
                   (id, case_id, storage_path, kind, source_kind, mode, mime_type, byte_size,
                    client_sha256, checksum_status, sha256_verified, original_filename)
                   values (%s, %s, %s, %s, %s, 'live', %s, %s, %s, %s, %s, %s)""",
                (
                    evidence_id,
                    case_id,
                    storage_path,
                    intent["kind"],
                    intent["source_kind"],
                    intent["mime_type"],
                    intent["byte_size"],
                    intent["client_sha256"],
                    "verified" if sha256_verified else "client_declared",
                    sha256_verified,
                    intent["filename"],
                ),
            )
            cursor.execute(
                """update public.cases
                   set state_version = state_version + 1,
                       content_revision = content_revision + 1,
                       status = 'collecting', current_draft_id = null, updated_at = now()
                   where id = %s and created_by_user_id = %s and state_version = %s
                   returning *""",
                (case_id, actor_id, expected_state_version),
            )
            updated = cursor.fetchone()
            if updated is None:
                cursor.execute(
                    "select state_version from public.cases where id = %s and created_by_user_id = %s",
                    (case_id, actor_id),
                )
                latest = cursor.fetchone()
                if latest is None:
                    return None
                raise StaleCaseError(latest["state_version"])
            cursor.execute(
                "update public.approvals set superseded_at = now() where case_id = %s and superseded_at is null",
                (case_id,),
            )
            cursor.execute(
                "update public.evidence_upload_intents set finalized_evidence_id = %s, finalized_at = now() where id = %s",
                (evidence_id, intent["id"]),
            )
            if intent["mime_type"].startswith(("image/", "video/")):
                from claim_api.media_workflow import enqueue_media
                enqueue_media(cursor, case_id, updated["content_revision"])
            cursor.execute(
                """insert into public.audit_events
                   (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                    content_revision_before, content_revision_after, metadata_json)
                   values (%s, %s, 'case.evidence_added', %s, %s, %s, %s, %s)""",
                (
                    case_id,
                    actor_id if intent["source_kind"] == "handler_upload" else None,
                    current["state_version"],
                    updated["state_version"],
                    current["content_revision"],
                    updated["content_revision"],
                    Jsonb({
                        "evidence_id": str(evidence_id),
                        "kind": intent["kind"],
                        "source_kind": intent["source_kind"],
                        "mime_type": intent["mime_type"],
                        "byte_size": intent["byte_size"],
                        "checksum_status": "verified" if sha256_verified else "client_declared",
                        **({"deposit_grant_id": str(intent["deposit_grant_id"])} if intent["deposit_grant_id"] else {}),
                    }),
                ),
            )
            from claim_api.garage_repository import enqueue_photo_reply
            enqueue_photo_reply(connection, updated, evidence_id, intent["kind"], intent["mime_type"],
                                deposit_grant_id=intent["deposit_grant_id"])
            return self._case_view(cursor, updated)

    def get_owned_evidence(self, actor_id: UUID, case_id: UUID, evidence_id: UUID) -> EvidenceView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """select e.* from public.evidence e
                   join public.cases c on c.id = e.case_id
                   where e.id = %s and e.case_id = %s and c.created_by_user_id = %s""",
                (evidence_id, case_id, actor_id),
            )
            row = cursor.fetchone()
            return None if row is None else EvidenceView.model_validate(row)

    def seed_g1_media(
        self, actor_id: UUID, case_id: UUID, expected_state_version: int,
        media: list[dict[str, object]], fixture_version: str,
    ) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("select * from public.cases where id = %s and created_by_user_id = %s for update", (case_id, actor_id))
            current = cursor.fetchone()
            if current is None:
                return None
            cursor.execute("select 1 from public.audit_events where case_id = %s and event_type = 'case.g1_media_seeded' limit 1", (case_id,))
            if cursor.fetchone() is not None:
                return self._case_view(cursor, current)
            if current["scenario_id"] != "g1":
                return None
            if current["state_version"] != expected_state_version:
                raise StaleCaseError(current["state_version"])
            for item in media:
                cursor.execute(
                    """insert into public.evidence
                       (id, case_id, storage_path, kind, source_kind, mode, mime_type,
                        byte_size, client_sha256, checksum_status, sha256_verified, role, display_order)
                       values (%s, %s, %s, %s, 'synthetic_g1', 'mock', %s, %s, %s, 'verified', %s, %s, %s)""",
                    (item["id"], case_id, item["storage_path"], item["kind"],
                     item["mime_type"], item["byte_size"], item["sha256"], item["sha256"],
                     item["role"], item["display_order"]),
                )
            cursor.execute("update public.approvals set superseded_at = now() where case_id = %s and superseded_at is null", (case_id,))
            cursor.execute(
                """update public.cases set state_version = state_version + 1,
                   content_revision = content_revision + 1, status = 'collecting',
                   current_draft_id = null, updated_at = now()
                   where id = %s returning *""", (case_id,),
            )
            updated = cursor.fetchone()
            cursor.execute(
                """insert into public.audit_events
                   (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                    content_revision_before, content_revision_after, metadata_json)
                   values (%s, %s, 'case.g1_media_seeded', %s, %s, %s, %s, %s)""",
                (case_id, actor_id, current["state_version"], updated["state_version"],
                 current["content_revision"], updated["content_revision"],
                 Jsonb({"fixture_version": fixture_version, "evidence_ids": [str(item["id"]) for item in media]})),
            )
            from claim_api.media_workflow import enqueue_media
            enqueue_media(cursor, case_id, updated["content_revision"])
            return self._case_view(cursor, updated)

    def receive_cctv_event(
        self,
        actor_id: UUID,
        case_id: UUID,
        fixture_event_id: str,
        expected_state_version: int,
        evidence_id: UUID,
        storage_path: str,
        byte_size: int,
        client_sha256: str,
        observation_payload: dict[str, object],
        fixture_version: str,
        provider_version: str,
        camera_request_id: UUID | None = None,
    ) -> CaseView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from public.cases where id = %s and created_by_user_id = %s for update",
                (case_id, actor_id),
            )
            current = cursor.fetchone()
            if current is None:
                return None
            cursor.execute(
                """select * from public.audit_events
                   where case_id = %s and event_type = 'case.cctv_received'
                     and metadata_json->>'fixture_event_id' = %s
                   order by occurred_at desc limit 1""",
                (case_id, fixture_event_id),
            )
            if cursor.fetchone() is not None:
                return self._case_view(cursor, current)
            if current["scenario_id"] != "complete":
                return None
            if current["state_version"] != expected_state_version:
                raise StaleCaseError(current["state_version"])

            if camera_request_id is not None:
                cursor.execute("""select * from public.case_camera_requests
                    where id=%s and case_id=%s for update""", (camera_request_id, case_id))
                camera_request = cursor.fetchone()
                if (camera_request is None or camera_request["fixture_event_id"] != fixture_event_id
                        or camera_request["status"] not in {"requested", "waiting"}):
                    raise ValueError("An approved simulated request for this fixture is required.")

            cursor.execute(
                """insert into public.evidence
                   (id, case_id, storage_path, kind, source_kind, mode, mime_type, byte_size, client_sha256, checksum_status, sha256_verified)
                   values (%s, %s, %s, 'cctv_frame', 'synthetic_cctv', 'mock', 'image/png', %s, %s, 'verified', %s)""",
                (evidence_id, case_id, storage_path, byte_size, client_sha256, client_sha256),
            )
            query_hash = hashlib.sha256(
                json.dumps(
                    {"fixture_event_id": fixture_event_id, "evidence_id": str(evidence_id)},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            cursor.execute(
                """insert into public.provider_results
                   (case_id, provider, mode, status, query_hash, source_version, payload_json, reason)
                   values (%s, 'mock_vision', 'mock', 'matched', %s, %s, %s, %s)
                   returning id""",
                (
                    case_id,
                    query_hash,
                    provider_version,
                    Jsonb(observation_payload),
                    "Fixed synthetic observations for the bundled CCTV still; vehicle identity is not established.",
                ),
            )
            provider_result_id = cursor.fetchone()["id"]
            if camera_request_id is not None:
                cursor.execute("""update public.case_camera_requests
                    set status='received', evidence_id=%s, received_at=now(),
                        status_actor_user_id=%s, status_changed_at=now()
                    where id=%s""", (evidence_id, actor_id, camera_request_id))
            cursor.execute(
                """update public.cases
                   set state_version = state_version + 1,
                       content_revision = content_revision + 1,
                       status = 'collecting', current_draft_id = null, updated_at = now()
                   where id = %s and created_by_user_id = %s and state_version = %s
                   returning *""",
                (case_id, actor_id, expected_state_version),
            )
            updated = cursor.fetchone()
            if updated is None:
                cursor.execute(
                    "select state_version from public.cases where id = %s and created_by_user_id = %s",
                    (case_id, actor_id),
                )
                latest = cursor.fetchone()
                if latest is None:
                    return None
                raise StaleCaseError(latest["state_version"])
            cursor.execute(
                "update public.approvals set superseded_at = now() where case_id = %s and superseded_at is null",
                (case_id,),
            )
            cursor.execute(
                """insert into public.audit_events
                   (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                    content_revision_before, content_revision_after, metadata_json)
                   values (%s, %s, 'case.cctv_received', %s, %s, %s, %s, %s)""",
                (
                    case_id,
                    actor_id,
                    current["state_version"],
                    updated["state_version"],
                    current["content_revision"],
                    updated["content_revision"],
                    Jsonb({
                        "fixture_event_id": fixture_event_id,
                        "fixture_version": fixture_version,
                        "evidence_id": str(evidence_id),
                        "provider_result_id": str(provider_result_id),
                        "provider_version": provider_version,
                        "camera_request_id": str(camera_request_id) if camera_request_id else None,
                    }),
                ),
            )
            from claim_api.media_workflow import enqueue_media
            enqueue_media(cursor, case_id, updated["content_revision"])
            return self._case_view(cursor, updated)

    def update_current_draft(
        self, actor_id: UUID, case_id: UUID, request: DraftEditRequest
    ) -> DraftView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            current = self._lock_owned_case(cursor, actor_id, case_id)
            if current is None:
                return None
            if current["state_version"] != request.expected_state_version:
                raise StaleCaseError(current["state_version"])
            if current["status"] in {"registered", "sent"}:
                raise ReviewTransitionError(
                    "invalid_transition", "A simulated registration has already been completed; this draft is read-only."
                )
            if current["current_draft_id"] is None:
                raise ReviewTransitionError("draft_required", "Run analysis before editing a draft.")

            old = self._load_current_draft(cursor, current)
            if old is None:
                raise ReviewTransitionError("draft_required", "The current draft is unavailable; refresh the case.")
            patch = request.model_dump(exclude={"expected_state_version"}, exclude_unset=True)
            recipient = old["recipient_json"]
            amount_minor = old["amount_minor"]
            currency = old["currency"]
            body = old["body"]
            attachment_ids = [UUID(str(item)) for item in old["attachment_ids"]]
            if "recipient" in patch:
                recipient = canonical_recipient(patch["recipient"])
                if recipient is not None and not isinstance(recipient, dict):
                    raise ReviewTransitionError("invalid_draft", "Recipient must be a JSON object or null.")
            if "amount_minor" in patch:
                amount_minor = patch["amount_minor"]
            if "currency" in patch:
                currency = patch["currency"]
            if "body" in patch:
                body = patch["body"] or ""
            if "attachment_ids" in patch:
                attachment_ids = patch["attachment_ids"] or []
            transmission_comment = old["transmission_comment"]
            if "transmission_comment" in patch:
                transmission_comment = (patch["transmission_comment"] or "").strip()
            if (amount_minor is None) != (currency is None):
                raise ReviewTransitionError(
                    "invalid_draft", "An amount and its ISO currency must either both be set or both be empty."
                )
            if len(set(attachment_ids)) != len(attachment_ids):
                raise ReviewTransitionError("invalid_draft", "An evidence attachment can appear only once.")
            if attachment_ids:
                cursor.execute(
                    "select id, mime_type from public.evidence where case_id = %s and id = any(%s)",
                    (case_id, attachment_ids),
                )
                attached_evidence = cursor.fetchall()
                found = {row["id"] for row in attached_evidence}
                if found != set(attachment_ids):
                    raise ReviewTransitionError(
                        "invalid_attachment", "Every attached evidence item must belong to this case.",
                        {"invalid_evidence_ids": [str(item) for item in set(attachment_ids) - found]},
                    )
                quote_row = self._latest_quote_row(cursor, case_id)
                quote_id = quote_row["evidence_id"] if quote_row else None
                if any(row["mime_type"] == "application/pdf" and row["id"] != quote_id
                       for row in attached_evidence):
                    raise ReviewTransitionError(
                        "invalid_attachment", "Only the PDF associated as the current quote may be attached to the draft."
                    )

            package = old["package_json"] or self._package_snapshot(cursor, current)
            sha256 = draft_digest(recipient, amount_minor, currency, body, attachment_ids, package, transmission_comment)
            old_sha256 = draft_digest(
                old["recipient_json"], old["amount_minor"], old["currency"], old["body"],
                old["attachment_ids"], old["package_json"], old["transmission_comment"]
            )
            if sha256 == old_sha256:
                return self._draft_view(old)

            gates = self._load_analysis_gates(cursor, case_id, old["analysis_run_id"])
            next_status = "review_ready" if self._upstream_gates_pass(gates) else "collecting"
            next_content_revision = current["content_revision"] + 1
            cursor.execute(
                "select coalesce(max(version), 0) + 1 as next_version from public.drafts where case_id = %s",
                (case_id,),
            )
            next_version = cursor.fetchone()["next_version"]
            cursor.execute(
                """insert into public.drafts
                   (case_id, analysis_run_id, parent_draft_id, version, content_revision,
                    recipient_json, amount_minor, currency, body, attachment_ids, sha256,
                    package_json, transmission_comment, created_by_user_id)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   returning *""",
                (
                    case_id,
                    old["analysis_run_id"],
                    old["id"],
                    next_version,
                    next_content_revision,
                    Jsonb(recipient) if recipient is not None else None,
                    amount_minor,
                    currency,
                    body,
                    Jsonb([str(item) for item in attachment_ids]),
                    sha256,
                    Jsonb(package), transmission_comment, actor_id,
                ),
            )
            new_draft = cursor.fetchone()
            cursor.execute(
                """update public.cases
                   set current_draft_id = %s, state_version = state_version + 1,
                       content_revision = content_revision + 1, status = %s, updated_at = now()
                   where id = %s and created_by_user_id = %s and state_version = %s
                   returning *""",
                (new_draft["id"], next_status, case_id, actor_id, request.expected_state_version),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise StaleCaseError(current["state_version"] + 1)
            cursor.execute(
                "update public.approvals set superseded_at = now() where case_id = %s and superseded_at is null",
                (case_id,),
            )
            cursor.execute(
                """insert into public.audit_events
                   (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                    content_revision_before, content_revision_after, metadata_json)
                   values (%s, %s, 'case.draft_edited', %s, %s, %s, %s, %s)""",
                (
                    case_id,
                    actor_id,
                    current["state_version"],
                    updated["state_version"],
                    current["content_revision"],
                    updated["content_revision"],
                    Jsonb({
                        "previous_draft_id": str(old["id"]),
                        "draft_id": str(new_draft["id"]),
                        "draft_version": next_version,
                        "draft_sha256": sha256,
                        "changed_fields": sorted(name for name in patch if name != "expected_state_version"),
                    }),
                ),
            )
            return self._draft_view(new_draft)

    def approve_current_draft(
        self, actor_id: UUID, case_id: UUID, request: ApproveDraftRequest
    ) -> ApprovalView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("select * from public.cases where id = %s for update", (case_id,))
            current = cursor.fetchone()
            if current is None:
                return None
            cursor.execute(
                """select role, can_approve from public.case_memberships
                   where case_id = %s and organization_id = %s and user_id = %s""",
                (case_id, current["organization_id"], actor_id),
            )
            membership = cursor.fetchone()
            if not membership or membership["role"] != "claims_handler" or not membership["can_approve"]:
                raise ReviewTransitionError("forbidden_approval", "The actor lacks claims_handler:approve for this case.")
            if not request.confirmed_review:
                raise ReviewTransitionError("review_confirmation_required", "Confirm that the report, sources, attachments, quote, total, and recipient were reviewed.", {"reason_codes": ["review_confirmation_required"]})
            draft = self._load_current_draft(cursor, current)
            if draft is None:
                raise ReviewTransitionError("draft_required", "Run analysis before approving a draft.")
            cursor.execute(
                "select * from public.approvals where case_id = %s and superseded_at is null for update",
                (case_id,),
            )
            active = cursor.fetchone()
            if (
                active is not None
                and request.draft_id == draft["id"]
                and request.draft_sha256 == draft["sha256"]
                and active["draft_id"] == draft["id"]
                and active["draft_sha256"] == draft["sha256"]
                and active["approved_content_revision"] == current["content_revision"]
                and draft["content_revision"] == current["content_revision"]
            ):
                return ApprovalView.model_validate(active)
            if current["state_version"] != request.expected_state_version:
                raise StaleCaseError(current["state_version"])
            if request.draft_id != draft["id"]:
                raise ReviewTransitionError(
                    "stale_draft", "Only the current draft version can be approved.",
                    {"current_draft_id": str(draft["id"])},
                )
            if request.draft_sha256 != draft["sha256"]:
                raise ReviewTransitionError("stale_draft", "The reviewed digest is no longer current.", {"current_draft_id": str(draft["id"]), "current_draft_sha256": draft["sha256"]})
            if draft["content_revision"] != current["content_revision"]:
                raise ReviewTransitionError("stale_draft", "The draft does not match the current case content revision.")
            if draft["package_json"] is None:
                raise ReviewTransitionError("gate_blocked", "Rebuild this legacy draft before approval.", {"reason_codes": ["package_rebuild_required"]})
            if draft["package_json"] != self._package_snapshot(cursor, current):
                raise ReviewTransitionError("gate_blocked", "The report or estimate changed after this draft was built.", {"reason_codes": ["package_snapshot_stale"]})
            if not self._draft_digest_matches(draft):
                raise ReviewTransitionError("invalid_draft", "The stored draft digest does not match its current content.")
            report_view = self._case_view(cursor, current)
            if (report_view.latest_analysis and report_view.latest_analysis.output
                    and not report_view.report.analysis_current):
                raise ReviewTransitionError(
                    "gate_blocked", "The analysis sources are stale or unavailable; rerun analysis.",
                    {"missing_fields": ["current_analysis_sources"]},
                )
            gates = self._load_analysis_gates(cursor, case_id, draft["analysis_run_id"])
            failed_gates = [gate for gate in gates if gate.get("status") != "passed"]
            if not self._upstream_gates_pass(gates):
                passed_gate_names = {gate.get("gate") for gate in gates if gate.get("status") == "passed"}
                raise ReviewTransitionError(
                    "gate_blocked", "Approval is blocked until analysis gates 1–3 all pass.",
                    {
                        "gate_results": gates,
                        "failed_gates": failed_gates,
                        "missing_gates": sorted({"intake", "counterparty", "evidence"} - passed_gate_names),
                    },
                )
            verified_recipient = self._verified_recipient(cursor, case_id)
            if not self._recipient_matches_verified(draft["recipient_json"], verified_recipient):
                raise ReviewTransitionError(
                    "gate_blocked",
                    "The draft recipient must match the correspondent verified by the current analysis.",
                    {"missing_fields": ["verified_recipient_match"]},
                )
            if not draft["recipient_json"] or not str(draft["body"]).strip():
                raise ReviewTransitionError(
                    "gate_blocked", "Complete the recipient and message body before approval.",
                    {"missing_fields": [
                        name for name, value in {"recipient": draft["recipient_json"], "body": draft["body"]}.items()
                        if not value or not str(value).strip()
                    ]},
                )
            if (draft["amount_minor"] is None) != (draft["currency"] is None):
                raise ReviewTransitionError("invalid_draft", "The draft amount and currency are inconsistent.")
            estimate_row = self._latest_estimate_row(cursor, case_id)
            quote_row = self._latest_quote_row(cursor, case_id)
            if estimate_row is None or quote_row is None or quote_row["evidence_id"] is None:
                missing = [name for name, present in (("estimate_required", estimate_row is not None), ("quote_required", quote_row is not None and quote_row["evidence_id"] is not None)) if not present]
                raise ReviewTransitionError("gate_blocked", "A current estimate and attached quote are required.", {"reason_codes": missing})
            if estimate_row and (draft["amount_minor"] != estimate_row["total_minor"] or draft["currency"] != "EUR"):
                raise ReviewTransitionError(
                    "gate_blocked", "The draft amount must match the current estimate.",
                    {"draft_amount_minor": draft["amount_minor"], "estimate_total_minor": estimate_row["total_minor"]},
                )
            if quote_row and quote_row["evidence_id"] is not None:
                if str(quote_row["evidence_id"]) not in {str(item) for item in draft["attachment_ids"]}:
                    raise ReviewTransitionError(
                        "gate_blocked", "Attach the current quote PDF to the draft before approval.",
                        {"quote_evidence_id": str(quote_row["evidence_id"])},
                    )
                if estimate_row is None or quote_row["attached_estimate_version"] != estimate_row["version"]:
                    raise ReviewTransitionError(
                        "gate_blocked", "The quote must be explicitly linked to the current estimate version.",
                        {"quote_estimate_version": quote_row["attached_estimate_version"],
                         "current_estimate_version": estimate_row["version"] if estimate_row else None},
                    )
                if quote_row["total_ttc_minor"] != estimate_row["total_minor"]:
                    raise ReviewTransitionError(
                        "gate_blocked", "The quote total differs from the current estimate.",
                        {"quote_total_minor": quote_row["total_ttc_minor"],
                         "estimate_total_minor": estimate_row["total_minor"]},
                    )
            manifest = self._attachment_manifest(cursor, case_id, draft["attachment_ids"])
            quote_attachment = next((item for item in manifest if item["id"] == str(quote_row["evidence_id"])), None)
            if quote_attachment is None or quote_attachment["mime_type"] != "application/pdf":
                raise ReviewTransitionError("gate_blocked", "The attached quote must be a finalized PDF.", {"reason_codes": ["quote_pdf_invalid"]})
            if active is not None:
                cursor.execute("update public.approvals set superseded_at = now() where id = %s", (active["id"],))
            cursor.execute(
                """insert into public.approvals
                   (case_id, draft_id, actor_user_id, draft_sha256, approved_content_revision)
                   values (%s, %s, %s, %s, %s) returning *""",
                (case_id, draft["id"], actor_id, draft["sha256"], current["content_revision"]),
            )
            approval = cursor.fetchone()
            cursor.execute(
                """update public.cases set status = 'approved', state_version = state_version + 1, updated_at = now()
                   where id = %s and state_version = %s returning *""",
                (case_id, request.expected_state_version),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise StaleCaseError(current["state_version"] + 1)
            self._insert_workflow_audit(
                cursor, case_id, actor_id, "case.draft_approved", current, updated,
                {"approval_id": str(approval["id"]), "draft_id": str(draft["id"]), "draft_sha256": draft["sha256"]},
            )
            return ApprovalView.model_validate(approval)

    def register_current_draft(
        self, actor_id: UUID, case_id: UUID, request: ActionRequest, idempotency_key: str
    ) -> ActionResult | None:
        return self._perform_mock_action(actor_id, case_id, request, idempotency_key, "registration")

    def send_current_draft(
        self, actor_id: UUID, case_id: UUID, request: ActionRequest, idempotency_key: str
    ) -> ActionResult | None:
        return self._perform_mock_action(actor_id, case_id, request, idempotency_key, "send")

    def transmission_preview(self, actor_id: UUID, case_id: UUID) -> TransmissionPreviewView | None:
        with self._connection() as connection, connection.cursor() as cursor:
            current = self._lock_owned_case(cursor, actor_id, case_id)
            if current is None:
                return None
            draft = self._load_current_draft(cursor, current)
            if draft is None or draft["content_revision"] != current["content_revision"] or not self._draft_digest_matches(draft):
                raise ReviewTransitionError("stale_draft", "Build and review the current package before transmission.")
            if draft["package_json"] != self._package_snapshot(cursor, current):
                raise ReviewTransitionError("stale_draft", "The package preview is stale; rebuild it.")
            cursor.execute("select * from public.approvals where case_id = %s and superseded_at is null", (case_id,))
            approval = cursor.fetchone()
            cursor.execute(
                """select * from public.actions where case_id = %s and kind = 'registration'
                   and status = 'confirmed' and draft_id = %s order by created_at desc limit 1""",
                (case_id, draft["id"]),
            )
            registration = cursor.fetchone()
            return self._transmission_projection(cursor, case_id, draft, approval, registration)

    def transmission_receipt(self, actor_id: UUID, case_id: UUID) -> dict[str, Any] | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("select 1 from public.cases where id = %s and created_by_user_id = %s", (case_id, actor_id))
            if cursor.fetchone() is None:
                return None
            cursor.execute(
                """select envelope_json from public.actions where case_id = %s and kind = 'send'
                   and status = 'confirmed' order by created_at desc, id desc limit 1""",
                (case_id,),
            )
            action = cursor.fetchone()
            if action is None or action["envelope_json"] is None:
                raise ReviewTransitionError("receipt_unavailable", "No immutable simulated send receipt exists yet.")
            return action["envelope_json"]

    def _transmission_projection(
        self, cursor: psycopg.Cursor[dict[str, Any]], case_id: UUID, draft: dict[str, Any],
        approval: dict[str, Any] | None, registration: dict[str, Any] | None,
    ) -> TransmissionPreviewView:
        if draft["package_json"] is None:
            raise ReviewTransitionError("gate_blocked", "Rebuild the package before transmission.", {"reason_codes": ["package_rebuild_required"]})
        return TransmissionPreviewView(
            case_id=case_id, draft_id=draft["id"], draft_sha256=draft["sha256"],
            content_revision=draft["content_revision"],
            approval_id=approval["id"] if approval else None,
            registration_action_id=registration["id"] if registration else None,
            registration_reference=registration["reference"] if registration else None,
            recipient=draft["recipient_json"], body=draft["body"],
            transmission_comment=draft["transmission_comment"],
            amount_minor=draft["amount_minor"], currency=draft["currency"],
            package=draft["package_json"],
            attachments=self._attachment_manifest(cursor, case_id, draft["attachment_ids"]),
        )

    def _perform_mock_action(
        self,
        actor_id: UUID,
        case_id: UUID,
        request: ActionRequest,
        idempotency_key: str,
        kind: str,
    ) -> ActionResult | None:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise ReviewTransitionError("invalid_idempotency_key", "Idempotency-Key must contain 1–200 characters.")
        with self._connection() as connection, connection.cursor() as cursor:
            current = self._lock_owned_case(cursor, actor_id, case_id)
            if current is None:
                return None
            draft = self._load_current_draft(cursor, current)
            cursor.execute(
                "select * from public.approvals where case_id = %s and superseded_at is null for update",
                (case_id,),
            )
            approval = cursor.fetchone()
            registration = None
            if kind == "send" and draft is not None and approval is not None:
                cursor.execute(
                    """select * from public.actions where case_id = %s and kind = 'registration'
                       and status = 'confirmed' and draft_id = %s and approval_id = %s
                       order by created_at, id limit 1""",
                    (case_id, draft["id"], approval["id"]),
                )
                registration = cursor.fetchone()
            existing_key = self._action_by_key(cursor, case_id, kind, key)
            if existing_key is not None:
                if draft is None or approval is None:
                    raise ReviewTransitionError(
                        "idempotency_conflict", "This Idempotency-Key belongs to an action whose draft is no longer current.",
                        {"action_id": str(existing_key["id"])},
                    )
                payload_hash = self._action_payload_hash(kind, case_id, draft, approval, registration)
                if existing_key["payload_hash"] != payload_hash:
                    raise ReviewTransitionError(
                        "idempotency_conflict", "This Idempotency-Key was already used for a different draft action.",
                        {"action_id": str(existing_key["id"])},
                    )
                return ActionResult(receipt=self._action_view(existing_key), created=False)
            if draft is None:
                raise ReviewTransitionError("draft_required", "Run analysis before taking this action.")
            if approval is None or not self._approval_matches(approval, draft, current):
                raise ReviewTransitionError("approval_required", "Approve the current draft before taking this action.")
            if draft["package_json"] != self._package_snapshot(cursor, current):
                raise ReviewTransitionError("gate_blocked", "The approved package is stale.", {"reason_codes": ["package_snapshot_stale"]})

            payload_hash = self._action_payload_hash(kind, case_id, draft, approval, registration)
            # A fresh key should still return the original receipt for an already completed action.
            cursor.execute(
                """select * from public.actions where case_id = %s and kind = %s and status = 'confirmed'
                   and draft_id = %s and approval_id = %s order by created_at, id limit 1""",
                (case_id, kind, draft["id"], approval["id"]),
            )
            completed = cursor.fetchone()
            if completed is not None:
                return ActionResult(receipt=self._action_view(completed), created=False)
            cursor.execute(
                """select id from public.actions where case_id = %s and kind = %s
                   and draft_id = %s and approval_id = %s and status = 'unknown' limit 1""",
                (case_id, kind, draft["id"], approval["id"]),
            )
            unresolved = cursor.fetchone()
            if unresolved is not None:
                raise ReviewTransitionError("reconciliation_required", "An earlier simulated action has an unknown outcome.", {"action_id": str(unresolved["id"]), "reason_codes": ["unknown_action_outcome"]})

            if current["state_version"] != request.expected_state_version:
                raise StaleCaseError(current["state_version"])

            expected_status = "approved" if kind == "registration" else "registered"
            if current["status"] != expected_status:
                if kind == "send" and registration is None:
                    raise ReviewTransitionError("registration_required", "Simulate registration before sending.")
                raise ReviewTransitionError(
                    "invalid_transition", f"Cannot simulate {kind} while the case is {current['status']}."
                )
            if kind == "send" and registration is None:
                raise ReviewTransitionError("registration_required", "Simulate registration before sending.")

            action_id = uuid4()
            draft_view = self._draft_view(draft)
            approval_view = ApprovalView.model_validate(approval)
            if kind == "registration":
                reference = self.claim_registry.register(draft_view, approval_view)
            else:
                assert registration is not None
                reference = self.claim_sender.send(draft_view, approval_view, self._action_view(registration))
            created_at = datetime.now(timezone.utc)
            envelope = None
            if kind == "send":
                projection = self._transmission_projection(cursor, case_id, draft, approval, registration)
                envelope = {
                    **projection.model_dump(mode="json"),
                    "send_action_id": str(action_id), "send_reference": reference,
                    "sent_at": created_at.isoformat(),
                }
            cursor.execute(
                """insert into public.actions
                   (id, case_id, approval_id, draft_id, registration_action_id, kind, mode, status,
                    idempotency_key, payload_hash, reference, created_at, envelope_json)
                   values (%s, %s, %s, %s, %s, %s, 'mock', 'confirmed', %s, %s, %s, %s, %s) returning *""",
                (
                    action_id,
                    case_id,
                    approval["id"],
                    draft["id"],
                    registration["id"] if registration else None,
                    kind,
                    key,
                    payload_hash,
                    reference,
                    created_at,
                    Jsonb(envelope) if envelope else None,
                ),
            )
            receipt = cursor.fetchone()
            next_status = "registered" if kind == "registration" else "sent"
            cursor.execute(
                """update public.cases set status = %s, state_version = state_version + 1, updated_at = now()
                   where id = %s and created_by_user_id = %s and state_version = %s returning *""",
                (next_status, case_id, actor_id, request.expected_state_version),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise StaleCaseError(current["state_version"] + 1)
            self._insert_workflow_audit(
                cursor,
                case_id,
                actor_id,
                f"case.{kind}_simulated",
                current,
                updated,
                {"action_id": str(action_id), "approval_id": str(approval["id"]), "draft_id": str(draft["id"]), "reference": reference},
            )
            return ActionResult(receipt=self._action_view(receipt), created=True)

    @staticmethod
    def _lock_owned_case(cursor: psycopg.Cursor[dict[str, Any]], actor_id: UUID, case_id: UUID) -> dict[str, Any] | None:
        cursor.execute(
            "select * from public.cases where id = %s and created_by_user_id = %s for update",
            (case_id, actor_id),
        )
        return cursor.fetchone()

    @staticmethod
    def _load_current_draft(cursor: psycopg.Cursor[dict[str, Any]], case_row: dict[str, Any]) -> dict[str, Any] | None:
        if case_row["current_draft_id"] is None:
            return None
        cursor.execute(
            "select * from public.drafts where id = %s and case_id = %s",
            (case_row["current_draft_id"], case_row["id"]),
        )
        return cursor.fetchone()

    @staticmethod
    def _load_analysis_gates(
        cursor: psycopg.Cursor[dict[str, Any]], case_id: UUID, analysis_run_id: UUID | None
    ) -> list[dict[str, Any]]:
        if analysis_run_id is None:
            return []
        cursor.execute(
            "select status, output_json from public.analysis_runs where id = %s and case_id = %s",
            (analysis_run_id, case_id),
        )
        run = cursor.fetchone()
        if run is None or run["status"] != "ready" or not isinstance(run["output_json"], dict):
            return []
        gates = run["output_json"].get("gate_results", [])
        if not isinstance(gates, list):
            return []
        return [dict(gate) for gate in gates if isinstance(gate, dict)]

    @staticmethod
    def _upstream_gates_pass(gates: list[dict[str, Any]]) -> bool:
        required = {"intake", "counterparty", "evidence"}
        if len(gates) != len(required):
            return False
        by_name = {gate.get("gate"): gate.get("status") for gate in gates}
        return set(by_name) == required and all(by_name[name] == "passed" for name in required)

    @staticmethod
    def _verified_recipient(
        cursor: psycopg.Cursor[dict[str, Any]], case_id: UUID
    ) -> dict[str, Any] | None:
        cursor.execute("""select l.correspondent_result_id, l.incident_date, l.active,
                              p.source_version,
                              c.intake_json->>'incident_at' as incident_at
                       from public.case_counterparty_lookups l
                       join public.cases c on c.id=l.case_id
                       join public.provider_results p on p.id=l.insurance_result_id
                       where l.case_id=%s""", (case_id,))
        selection = cursor.fetchone()
        if selection:
            if not selection["active"] or selection["source_version"] != MOCK_INSURANCE_SOURCE_VERSION or not selection["correspondent_result_id"] or not selection["incident_at"] or str(selection["incident_date"]) != selection["incident_at"][:10]:
                return None
            cursor.execute("""select status,payload_json from public.provider_results
                              where case_id=%s and id=%s""", (case_id, selection["correspondent_result_id"]))
            result = cursor.fetchone()
            return result["payload_json"] if result and result["status"] == "matched" else None
        cursor.execute(
            """select status, payload_json from public.provider_results
               where case_id = %s and provider = 'correspondent_lookup'
               order by retrieved_at desc, id desc limit 1""",
            (case_id,),
        )
        result = cursor.fetchone()
        if result is None or result["status"] != "matched" or not isinstance(result["payload_json"], dict):
            return None
        return result["payload_json"]

    @staticmethod
    def _recipient_matches_verified(
        recipient: dict[str, Any] | None, verified: dict[str, Any] | None
    ) -> bool:
        if not recipient or not verified:
            return False
        proposed_name = next(
            (recipient.get(key) for key in ("name", "correspondent_name", "recipient_name") if recipient.get(key)),
            None,
        )
        verified_name = verified.get("correspondent_name")
        proposed_country = recipient.get("country")
        verified_country = verified.get("country")
        if not all(isinstance(value, str) and value.strip() for value in (
            proposed_name, verified_name, proposed_country, verified_country
        )):
            return False
        return (
            " ".join(proposed_name.split()).casefold() == " ".join(verified_name.split()).casefold()
            and proposed_country.strip().upper() == verified_country.strip().upper()
        )

    @staticmethod
    def _draft_digest_matches(draft: dict[str, Any]) -> bool:
        return draft_digest(
            draft["recipient_json"],
            draft["amount_minor"],
            draft["currency"],
            draft["body"],
            draft["attachment_ids"],
            draft["package_json"],
            draft["transmission_comment"],
        ) == draft["sha256"]

    @staticmethod
    def _approval_matches(approval: dict[str, Any], draft: dict[str, Any], case_row: dict[str, Any]) -> bool:
        return (
            approval["draft_id"] == draft["id"]
            and approval["draft_sha256"] == draft["sha256"]
            and approval["approved_content_revision"] == case_row["content_revision"]
            and draft["content_revision"] == case_row["content_revision"]
            and PostgresCaseRepository._draft_digest_matches(draft)
        )

    @staticmethod
    def _action_by_key(cursor: psycopg.Cursor[dict[str, Any]], case_id: UUID, kind: str, key: str) -> dict[str, Any] | None:
        cursor.execute(
            "select * from public.actions where case_id = %s and kind = %s and idempotency_key = %s",
            (case_id, kind, key),
        )
        return cursor.fetchone()

    @staticmethod
    def _action_payload_hash(
        kind: str,
        case_id: UUID,
        draft: dict[str, Any],
        approval: dict[str, Any],
        registration: dict[str, Any] | None,
    ) -> str:
        payload = {
            "kind": kind,
            "case_id": str(case_id),
            "draft_id": str(draft["id"]),
            "draft_sha256": draft["sha256"],
            "approval_id": str(approval["id"]),
            "registration_action_id": str(registration["id"]) if registration else None,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _insert_workflow_audit(
        cursor: psycopg.Cursor[dict[str, Any]],
        case_id: UUID,
        actor_id: UUID,
        event_type: str,
        before: dict[str, Any],
        after: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        cursor.execute(
            """insert into public.audit_events
               (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                content_revision_before, content_revision_after, metadata_json)
               values (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                case_id,
                actor_id,
                event_type,
                before["state_version"],
                after["state_version"],
                before["content_revision"],
                after["content_revision"],
                Jsonb(metadata),
            ),
        )

    def _package_snapshot(self, cursor: psycopg.Cursor[dict[str, Any]], case_row: dict[str, Any]) -> dict[str, Any]:
        """Capture the material S03/S04 content at the version boundary."""
        view = self._case_view(cursor, case_row)
        return {
            "schema_version": 1,
            "report_lines": [
                {"id": line.id, "text": line.text, "uncertainty": line.uncertainty,
                 "source_refs": [ref.model_dump(mode="json") for ref in line.source_refs]}
                for line in view.report.lines
            ],
            "estimate": view.estimate.model_dump(mode="json") if view.estimate else None,
            "quote": view.quote.model_dump(mode="json") if view.quote else None,
            "video_analyses": [item.model_dump(mode="json") for item in view.video_analyses],
        }

    @staticmethod
    def _attachment_manifest(
        cursor: psycopg.Cursor[dict[str, Any]], case_id: UUID, attachment_ids: list[str] | list[UUID]
    ) -> list[dict[str, Any]]:
        ids = [UUID(str(value)) for value in attachment_ids]
        if len(ids) != len(set(ids)):
            raise ReviewTransitionError("gate_blocked", "Attachment IDs must be unique.", {"reason_codes": ["duplicate_attachment"]})
        if not ids:
            return []
        cursor.execute("select * from public.evidence where case_id = %s and id = any(%s)", (case_id, ids))
        by_id = {row["id"]: row for row in cursor.fetchall()}
        if set(ids) != set(by_id):
            raise ReviewTransitionError("gate_blocked", "An attachment is no longer part of this case.", {"reason_codes": ["attachment_missing"]})
        manifest = []
        for evidence_id in ids:
            row = by_id[evidence_id]
            checksum = row["sha256_verified"] or row["client_sha256"]
            if not checksum or len(checksum) != 64:
                raise ReviewTransitionError("gate_blocked", "An attachment has no usable checksum.", {"reason_codes": ["attachment_checksum_missing"]})
            if row["mime_type"].startswith("video/") and row["checksum_status"] != "verified":
                raise ReviewTransitionError("gate_blocked", "A local video must be finalized before inclusion.", {"reason_codes": ["video_not_finalized"]})
            manifest.append({
                "id": str(evidence_id), "kind": row["kind"], "mime_type": row["mime_type"],
                "filename": row["original_filename"], "byte_size": row["byte_size"],
                "checksum": checksum, "checksum_status": row["checksum_status"],
            })
        return manifest

    @staticmethod
    def _draft_view(row: dict[str, Any]) -> DraftView:
        return DraftView(
            id=row["id"],
            case_id=row["case_id"],
            analysis_run_id=row["analysis_run_id"],
            parent_draft_id=row["parent_draft_id"],
            version=row["version"],
            content_revision=row["content_revision"],
            recipient=row["recipient_json"],
            amount_minor=row["amount_minor"],
            currency=row["currency"],
            body=row["body"],
            attachment_ids=[UUID(str(item)) for item in row["attachment_ids"]],
            package=row["package_json"],
            transmission_comment=row["transmission_comment"],
            created_by_user_id=row["created_by_user_id"],
            sha256=row["sha256"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _estimate_view(row: dict[str, Any]) -> EstimateView:
        return EstimateView(
            id=row["id"], case_id=row["case_id"], version=row["version"],
            line_items=[EstimateItem.model_validate(item) for item in row["line_items_json"]],
            total_minor=row["total_minor"], currency=row["currency"],
            tax_basis=row["tax_basis"], estimate_source=row["estimate_source"],
            source_refs=[AnalysisSourceRef.model_validate(ref) for ref in row["source_refs_json"]],
            created_by_user_id=row["created_by_user_id"], created_at=row["created_at"],
        )

    @staticmethod
    def _quote_view(row: dict[str, Any], evidence: EvidenceView) -> QuoteView:
        return QuoteView(
            id=row["id"], case_id=row["case_id"], version=row["version"],
            evidence_id=evidence.id,
            filename=evidence.original_filename or "Nom indisponible (ancien dépôt)",
            mime_type=evidence.mime_type,
            checksum=evidence.client_sha256 or "",
            checksum_status=evidence.checksum_status,
            total_ttc_minor=row["total_ttc_minor"], amount_source=row["amount_source"],
            attached_estimate_version=row["attached_estimate_version"],
            created_by_user_id=row["created_by_user_id"], created_at=row["created_at"],
        )

    @staticmethod
    def _action_view(row: dict[str, Any]) -> ActionReceiptView:
        return ActionReceiptView(
            id=row["id"],
            case_id=row["case_id"],
            kind=row["kind"],
            mode=row["mode"],
            status=row["status"],
            approval_id=row["approval_id"],
            draft_id=row["draft_id"],
            idempotency_key=row["idempotency_key"],
            reference=row["reference"],
            created_at=row["created_at"],
            registration_action_id=row["registration_action_id"],
            envelope=row["envelope_json"],
        )

    def _summary(self, row: dict[str, Any]) -> CaseListItem:
        return CaseListItem(
            id=row["id"],
            created_by_user_id=row["created_by_user_id"],
            scenario_id=row["scenario_id"],
            synthetic=row["synthetic"],
            status=row["status"],
            state_version=row["state_version"],
            content_revision=row["content_revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            intake=Intake.model_validate(row["intake_json"]),
        )

    def _provider_results(self, cursor: psycopg.Cursor[dict[str, Any]], case_id: UUID) -> list[ProviderResultView]:
        cursor.execute(
            "select * from public.provider_results where case_id = %s order by retrieved_at, id",
            (case_id,),
        )
        return [
            ProviderResultView(
                id=row["id"],
                source_id=row["id"],
                provider=row["provider"],
                mode=row["mode"],
                status=row["status"],
                source_version=row["source_version"],
                query_hash=row["query_hash"],
                query=row["query_json"],
                retrieved_at=row["retrieved_at"],
                data=row["payload_json"],
                reason=row["reason"],
            )
            for row in cursor.fetchall()
        ]

    def _case_view(self, cursor: psycopg.Cursor[dict[str, Any]], row: dict[str, Any]) -> CaseView:
        cursor.execute("""select l.*,p.source_version as insurance_source_version
                          from public.case_counterparty_lookups l
                          join public.provider_results p on p.id=l.insurance_result_id
                          where l.case_id=%s""", (row["id"],))
        lookup_row = cursor.fetchone()
        lookup = None
        if lookup_row:
            incident_at = row["intake_json"].get("incident_at")
            lookup = CounterpartyLookupView(
                plate_candidate=lookup_row["plate_candidate"], country=lookup_row["country"],
                incident_date=lookup_row["incident_date"], vehicle_track_id=lookup_row["vehicle_track_id"],
                identification_status=lookup_row["identification_status"],
                supporting_source_refs=lookup_row["supporting_source_refs_json"],
                validation_reason=lookup_row["validation_reason"],
                validated_by_user_id=lookup_row["validated_by_user_id"],
                validated_at=lookup_row["validated_at"],
                insurance_result_id=lookup_row["insurance_result_id"],
                correspondent_result_id=lookup_row["correspondent_result_id"],
                current=bool(lookup_row["active"] and lookup_row["insurance_source_version"] == MOCK_INSURANCE_SOURCE_VERSION
                             and incident_at and str(lookup_row["incident_date"]) == incident_at[:10]),
            )
        cursor.execute(
            "select * from public.case_camera_requests where case_id=%s order by created_at, id",
            (row["id"],),
        )
        camera_requests = [CameraRequestView.model_validate(item) for item in cursor.fetchall()]
        cursor.execute(
            "select * from public.voice_sessions where case_id = %s",
            (row["id"],),
        )
        voice_row = cursor.fetchone()
        voice_session = None
        if voice_row is not None:
            triage = voice_row["triage_json"]
            voice_session = VoiceCaseView(
                provider=voice_row["provider"], session_id=voice_row["provider_call_id"],
                mode=voice_row["mode"], telephony_provider=voice_row["telephony_provider"],
                status=triage["status"],
                missing_p0=triage.get("missing_p0", []),
                reason_codes=triage.get("reason_codes", []),
                facts=voice_row["facts_json"], segments=voice_row["transcript_json"],
                recording={"status": voice_row["recording_status"],
                           "mime_type": voice_row["recording_mime_type"],
                           "byte_size": voice_row["recording_byte_size"],
                           "sha256": voice_row["recording_sha256"],
                           "error_code": voice_row["recording_error_code"]},
                call_started_at=voice_row["call_started_at"],
            )
        cursor.execute("select * from public.evidence where case_id = %s order by display_order nulls last, received_at, id", (row["id"],))
        evidence = [EvidenceView.model_validate(item) for item in cursor.fetchall()]
        estimate_row = self._latest_estimate_row(cursor, row["id"])
        estimate = self._estimate_view(estimate_row) if estimate_row else None
        quote_row = self._latest_quote_row(cursor, row["id"])
        quote_evidence = next((item for item in evidence if quote_row and item.id == quote_row["evidence_id"]), None)
        quote = self._quote_view(quote_row, quote_evidence) if quote_row and quote_evidence else None
        cursor.execute(
            """select distinct on (line_id) * from public.report_line_edits
               where case_id = %s order by line_id, as_of_revision desc""",
            (row["id"],),
        )
        report_edits = cursor.fetchall()
        cursor.execute(
            """select l.evidence_id, a.id as artifact_id, a.pipeline_fingerprint,
                      a.observations_json as observations
               from public.case_media_analysis_links l
               join public.media_analysis_artifacts a on a.id=l.artifact_id and a.status='succeeded'
               where l.case_id=%s order by l.linked_at, l.evidence_id""",
            (row["id"],),
        )
        video_analyses = [VideoAnalysisLinkView.model_validate(item) for item in cursor.fetchall()]

        cursor.execute(
            "select * from public.analysis_runs where case_id = %s order by started_at desc limit 1",
            (row["id"],),
        )
        analysis_row = cursor.fetchone()
        latest_analysis = None
        upstream_gates: list[dict[str, Any]] = []
        if analysis_row is not None:
            envelope = analysis_row["output_json"] or {}
            candidate_gates = envelope.get("gate_results", []) if isinstance(envelope, dict) else []
            upstream_gates = candidate_gates if isinstance(candidate_gates, list) else []
            latest_analysis = {
                "id": analysis_row["id"],
                "input_content_revision": analysis_row["input_content_revision"],
                "method_version": analysis_row["method_version"],
                "status": analysis_row["status"],
                "mode": envelope.get("mode", "live") if isinstance(envelope, dict) else "live",
                "output": (envelope.get("output") or None) if isinstance(envelope, dict) else None,
                "gate_results": upstream_gates,
                "error_code": analysis_row["error_code"],
                "error_message": envelope.get("error_message") if isinstance(envelope, dict) else None,
                "started_at": analysis_row["started_at"],
                "finished_at": analysis_row["finished_at"],
            }

        current_draft = None
        if row["current_draft_id"] is not None:
            cursor.execute(
                "select * from public.drafts where case_id = %s and id = %s",
                (row["id"], row["current_draft_id"]),
            )
            draft_row = cursor.fetchone()
            current_draft = None if draft_row is None else self._draft_view(draft_row).model_dump(mode="json")

        cursor.execute(
            "select * from public.approvals where case_id = %s and superseded_at is null order by approved_at desc limit 1",
            (row["id"],),
        )
        approval_row = cursor.fetchone()
        approval = None if approval_row is None else ApprovalView.model_validate(approval_row).model_dump(mode="json")

        cursor.execute("select * from public.actions where case_id = %s order by created_at, id", (row["id"],))
        actions = [self._action_view(item).model_dump(mode="json") for item in cursor.fetchall()]

        cursor.execute(
            "select * from public.audit_events where case_id = %s order by state_version_after, occurred_at, id",
            (row["id"],),
        )
        timeline = [
            AuditEventView(
                id=item["id"],
                actor_user_id=item["actor_user_id"],
                event_type=item["event_type"],
                state_version_before=item["state_version_before"],
                state_version_after=item["state_version_after"],
                content_revision_before=item["content_revision_before"],
                content_revision_after=item["content_revision_after"],
                metadata=item["metadata_json"],
                occurred_at=item["occurred_at"],
            )
            for item in cursor.fetchall()
        ]

        gate_results = [dict(gate) for gate in upstream_gates if isinstance(gate, dict)]
        verified_recipient = self._verified_recipient(cursor, row["id"])
        gate_results.append(self._approval_gate(
            row, current_draft, approval, gate_results, verified_recipient,
            estimate, quote, quote_status(estimate, quote),
        ))

        case_view = CaseView(
            **self._summary(row).model_dump(),
            evidence=evidence,
            camera_requests=camera_requests,
            video_analyses=video_analyses,
            provider_results=self._provider_results(cursor, row["id"]),
            counterparty_lookup=lookup,
            latest_analysis=latest_analysis,
            current_draft=current_draft,
            gate_results=gate_results,
            approval=approval,
            actions=actions,
            timeline=timeline,
            estimate=estimate,
            quote=quote,
            quote_status=quote_status(estimate, quote),
            voice_session=voice_session,
        )
        return case_view.model_copy(update={"report": build_report(case_view, report_edits)})

    @staticmethod
    def _approval_gate(
        case_row: dict[str, Any],
        draft: dict[str, Any] | None,
        approval: dict[str, Any] | None,
        upstream_gates: list[dict[str, Any]],
        verified_recipient: dict[str, Any] | None,
        estimate: EstimateView | None,
        quote: QuoteView | None,
        current_quote_status: str,
    ) -> dict[str, Any]:
        if not estimate:
            return {"gate": "approval", "status": "blocked", "reason_codes": ["estimate_required"], "source_refs": []}
        if not quote:
            return {"gate": "approval", "status": "blocked", "reason_codes": ["quote_required"], "source_refs": []}
        if quote and current_quote_status in {"no_estimate", "mismatch", "outdated"}:
            return {"gate": "approval", "status": "blocked", "reason_codes": [f"quote_{current_quote_status}"], "source_refs": []}
        if quote and draft and str(quote.evidence_id) not in {str(item) for item in draft.get("attachment_ids", [])}:
            return {"gate": "approval", "status": "blocked", "reason_codes": ["quote_attachment_required"], "source_refs": []}
        if draft and estimate and (draft.get("amount_minor") != estimate.total_minor or draft.get("currency") != "EUR"):
            return {"gate": "approval", "status": "blocked", "reason_codes": ["estimate_draft_mismatch"], "source_refs": []}
        if not draft:
            return {"gate": "approval", "status": "blocked", "reason_codes": ["draft_required"], "source_refs": []}
        if not PostgresCaseRepository._upstream_gates_pass(upstream_gates):
            return {
                "gate": "approval",
                "status": "blocked",
                "reason_codes": ["upstream_gates_not_passed"],
                "source_refs": [],
            }
        if not PostgresCaseRepository._recipient_matches_verified(draft.get("recipient"), verified_recipient):
            return {
                "gate": "approval",
                "status": "blocked",
                "reason_codes": ["unverified_recipient"],
                "source_refs": [],
            }
        if not draft.get("recipient") or not str(draft.get("body", "")).strip():
            return {
                "gate": "approval",
                "status": "needs_review",
                "reason_codes": ["draft_incomplete"],
                "source_refs": [],
            }
        if draft.get("content_revision") != case_row["content_revision"]:
            return {"gate": "approval", "status": "blocked", "reason_codes": ["draft_revision_stale"], "source_refs": []}
        if draft.get("package") is None:
            return {"gate": "approval", "status": "blocked", "reason_codes": ["package_rebuild_required"], "source_refs": []}
        if draft_digest(
            draft.get("recipient"),
            draft.get("amount_minor"),
            draft.get("currency"),
            str(draft.get("body", "")),
            draft.get("attachment_ids", []),
            draft.get("package"),
            str(draft.get("transmission_comment", "")),
        ) != draft.get("sha256"):
            return {"gate": "approval", "status": "blocked", "reason_codes": ["draft_digest_invalid"], "source_refs": []}
        if (
            approval
            and approval.get("draft_id") == str(draft.get("id"))
            and approval.get("draft_sha256") == draft.get("sha256")
            and approval.get("approved_content_revision") == case_row["content_revision"]
        ):
            return {"gate": "approval", "status": "passed", "reason_codes": [], "source_refs": []}
        return {
            "gate": "approval",
            "status": "needs_review",
            "reason_codes": ["handler_approval_required"],
            "source_refs": [],
        }
