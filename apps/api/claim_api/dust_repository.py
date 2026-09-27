"""Durable Dust jobs; no edits to claim decisions, approvals or media analyses."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.dust_client import DustError
from claim_api.dust_models import DustRunView
from claim_api.postgres_cases import PostgresCaseRepository


class PostgresDustRepository(PostgresCaseRepository):
    @staticmethod
    def view(row, current_revision):
        values = {key: row.get(key) for key in DustRunView.model_fields}
        if row["input_content_revision"] != current_revision:
            values.update(status="stale", output=None)
        return DustRunView.model_validate(values)

    def find_request(self, actor, case_id, key):
        with self._connection() as conn:
            return conn.execute(
                """select d.* from public.dust_runs d
                join public.cases c on c.id=d.case_id
                where d.case_id=%s and c.created_by_user_id=%s and d.idempotency_key=%s""",
                (case_id, actor, key),
            ).fetchone()

    def get_run(self, actor, case_id, run_id):
        with self._connection() as conn:
            return conn.execute(
                """select d.* from public.dust_runs d
                join public.cases c on c.id=d.case_id
                where d.case_id=%s and c.created_by_user_id=%s and d.id=%s""",
                (case_id, actor, run_id),
            ).fetchone()

    def list_runs(self, actor, case_id):
        with self._connection() as conn:
            rows = conn.execute(
                """select d.*, c.content_revision as current_revision
                from public.dust_runs d join public.cases c on c.id=d.case_id
                where d.case_id=%s and c.created_by_user_id=%s
                order by d.created_at desc, d.id desc limit 100""",
                (case_id, actor),
            ).fetchall()
            return [self.view(row, row["current_revision"]) for row in rows]

    def reserve(self, actor, case, request, digest, snapshot, client):
        with self._connection() as conn:
            current = conn.execute(
                "select * from public.cases where id=%s and created_by_user_id=%s for update",
                (case.id, actor),
            ).fetchone()
            if current is None:
                raise CaseNotFoundError
            existing = conn.execute(
                "select * from public.dust_runs where case_id=%s and idempotency_key=%s",
                (case.id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise DustError("dust_idempotency_conflict")
                return existing, False
            if (
                current["state_version"] != request.expected_state_version
                or current["content_revision"] != case.content_revision
            ):
                raise StaleCaseError(current["state_version"])
            # A changed dossier releases old jobs, but never reuses their output.
            conn.execute(
                """update public.dust_runs set status='stale', output=null, updated_at=now()
                where case_id=%s and input_content_revision<>%s and status in ('submitting','running','needs_action')""",
                (case.id, case.content_revision),
            )
            active = conn.execute(
                """select id from public.dust_runs where case_id=%s and agent=%s
                and status in ('submitting','running','needs_action') limit 1""",
                (case.id, request.agent),
            ).fetchone()
            if active:
                raise DustError("dust_agent_in_progress")
            row = conn.execute(
                """insert into public.dust_runs
                (id, case_id, agent, agent_id, workspace_id, base_url, input_content_revision,
                 idempotency_key, request_hash, gemini_analysis_run_id, input_json, status)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'submitting') returning *""",
                (
                    uuid4(),
                    case.id,
                    request.agent,
                    client.agents[request.agent],
                    client.workspace_id,
                    client.base_url,
                    case.content_revision,
                    request.idempotency_key,
                    digest,
                    snapshot["gemini_analysis_run_id"],
                    Jsonb(snapshot),
                ),
            ).fetchone()
            self._audit(conn, actor, current, row, "case.dust_started")
            return row, True

    def update_run(
        self,
        actor,
        case_id,
        run_id,
        *,
        status,
        output=None,
        error_code=None,
        conversation_id=None
    ):
        with self._connection() as conn:
            case = conn.execute(
                "select * from public.cases where id=%s and created_by_user_id=%s for update",
                (case_id, actor),
            ).fetchone()
            if case is None:
                raise CaseNotFoundError
            row = conn.execute(
                "select * from public.dust_runs where case_id=%s and id=%s for update",
                (case_id, run_id),
            ).fetchone()
            if row is None:
                raise CaseNotFoundError
            # Concurrent refreshes cannot overwrite a terminal result.
            if row["status"] in {"ready", "failed", "stale"}:
                return self.view(row, case["content_revision"])
            if row["input_content_revision"] != case["content_revision"]:
                status, output, error_code = "stale", None, "stale_input_revision"
            old_status = row["status"]
            row = conn.execute(
                """update public.dust_runs set status=%s, output=%s, error_code=%s,
                conversation_id=coalesce(%s, conversation_id), updated_at=now()
                where id=%s returning *""",
                (
                    status,
                    Jsonb(output.model_dump(mode="json")) if output else None,
                    error_code,
                    conversation_id,
                    run_id,
                ),
            ).fetchone()
            if old_status != status:
                self._audit(conn, actor, case, row, "case.dust_updated")
            return self.view(row, case["content_revision"])

    @staticmethod
    def _audit(conn, actor, case, run, event):
        conn.execute(
            """insert into public.audit_events
            (case_id,actor_user_id,event_type,state_version_before,state_version_after,
             content_revision_before,content_revision_after,metadata_json)
            values (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                case["id"],
                actor,
                event,
                case["state_version"],
                case["state_version"],
                case["content_revision"],
                case["content_revision"],
                Jsonb(
                    {
                        "dust_run_id": str(run["id"]),
                        "agent": run["agent"],
                        "status": run["status"],
                    }
                ),
            ),
        )
