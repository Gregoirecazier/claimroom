"""Durable automatic Astra journey; legacy orchestration retained for historical runs."""

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from psycopg.types.json import Jsonb

from claim_api.analysis import AnalysisInProgressError, UrgentHandoffError
from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.dust_client import DustClient, DustError
from claim_api.dust_models import DustRunRequest
from claim_api.dust_repository import PostgresDustRepository
from claim_api.dust_service import DustService
from claim_api.media_analysis import MediaAnalysisService
from claim_api.media_insurance import lookup_media_plates
from claim_api.models import AnalysisRunRequest
from claim_api.postgres_cases import PostgresCaseRepository

logger = logging.getLogger(__name__)


def enqueue_media(cursor, case_id, revision):
    cursor.execute(
        """insert into public.case_media_workflows(case_id,content_revision)
        values (%s,%s) on conflict (case_id,content_revision) do nothing""",
        (case_id, revision),
    )


class MediaWorkflowRepository(PostgresCaseRepository):
    def enqueue(self, actor, case, retry=False):
        with self._connection() as conn:
            current = conn.execute(
                "select * from public.cases where id=%s and created_by_user_id=%s for update",
                (case.id, actor),
            ).fetchone()
            if current is None:
                raise CaseNotFoundError
            if current["content_revision"] != case.content_revision:
                raise StaleCaseError(current["state_version"])
            enqueue_media(conn, case.id, case.content_revision)
            if retry:
                failed = conn.execute(
                    """select w.id,w.garage_run_id,d.status as dust_status
                    from public.case_media_workflows w left join public.dust_runs d on d.id=w.garage_run_id
                    where w.case_id=%s and w.content_revision=%s and w.status='failed' for update of w""",
                    (case.id, case.content_revision),
                ).fetchone()
                if failed is None:
                    return
                # A failed GET (permissions, network) does not terminate the remote
                # conversation. An explicit retry must resume that same Dust run.
                resume = failed["dust_status"] in {
                    "submitting",
                    "running",
                    "needs_action",
                    "submission_unknown",
                }
                conn.execute(
                    """update public.case_media_workflows set status='queued',error_code=null,
                    generation=generation+%s,garage_run_id=%s,attempts=0,available_at=now()
                    where id=%s""",
                    (
                        0 if resume else 1,
                        failed["garage_run_id"] if resume else None,
                        failed["id"],
                    ),
                )

    def claim(self, case_id=None):
        lease = uuid4()
        with self._connection() as conn:
            conn.execute("""update public.case_media_workflows w set status='stale',updated_at=now()
                from public.cases c where w.case_id=c.id and w.content_revision<>c.content_revision
                and w.status in ('queued','waiting') and (%s::uuid is null or w.case_id=%s)""", (case_id,case_id))
            return conn.execute(
                """with next_job as (
                select w.id from public.case_media_workflows w
                where (%s::uuid is null or w.case_id=%s) and
                  ((w.status in ('queued','waiting') and w.available_at<=now())
                   or (w.status='processing' and w.lease_until<now()))
                order by w.content_revision desc,w.available_at,w.created_at for update skip locked limit 1)
                update public.case_media_workflows w set status='processing',lease_id=%s,
                lease_until=now()+interval '6 minutes',attempts=attempts+1,updated_at=now()
                from next_job n,public.cases c where w.id=n.id and c.id=w.case_id
                returning w.*,c.created_by_user_id as actor_id""",
                (case_id, case_id, lease),
            ).fetchone()

    def finish(
        self,
        job,
        status,
        *,
        error=None,
        matches=None,
        analysis_id=None,
        garage_id=None,
        delay_seconds=5,
    ):
        with self._connection() as conn:
            conn.execute(
                """update public.case_media_workflows set status=%s,error_code=%s,
                insurance_matches=coalesce(%s,insurance_matches),
                analysis_run_id=coalesce(%s,analysis_run_id),garage_run_id=coalesce(%s,garage_run_id),
                available_at=now()+(%s * interval '1 second'),lease_id=null,lease_until=null,updated_at=now()
                where id=%s and lease_id=%s""",
                (
                    status,
                    error,
                    Jsonb(matches) if matches is not None else None,
                    analysis_id,
                    garage_id,
                    delay_seconds,
                    job["id"],
                    job["lease_id"],
                ),
            )

    def checkpoint(self, job, matches, analysis_id):
        with self._connection() as conn:
            conn.execute(
                """update public.case_media_workflows set insurance_matches=%s,analysis_run_id=%s
                where id=%s and lease_id=%s""",
                (Jsonb(matches), analysis_id, job["id"], job["lease_id"]),
            )

    def save_garage_draft(self, actor, case, run):
        subject = f"Demande de devis de réparation — dossier {str(case.id)[:8]}"
        body = run.output.draft_body
        if case.synthetic:
            subject = "[Démonstration] " + subject
            body = (
                "Dossier fictif de démonstration : aucune commande ni prise en charge n’est engagée.\n\n"
                + body
            )
        with self._connection() as conn:
            current = conn.execute(
                "select content_revision from public.cases where id=%s and created_by_user_id=%s for update",
                (case.id, actor),
            ).fetchone()
            if not current or current["content_revision"] != run.input_content_revision:
                return
            conn.execute(
                """insert into public.case_correspondence
                (case_id,kind,content_revision,source_run_id,subject,body)
                values (%s,'garage',%s,%s,%s,%s) on conflict (source_run_id) do nothing""",
                (case.id, case.content_revision, run.id, subject, body),
            )

    def snapshot(self, actor, case_id):
        case = self.get_case(actor, case_id)
        if case is None:
            raise CaseNotFoundError
        with self._connection() as conn:
            job = conn.execute(
                """select id,content_revision,status,error_code,analysis_run_id,garage_run_id,
                insurance_matches,updated_at from public.case_media_workflows
                where case_id=%s and content_revision=%s""",
                (case_id, case.content_revision),
            ).fetchone()
            drafts = conn.execute(
                """select id,kind,content_revision,recipient,subject,body,source_url,version,status,
                provider_message_id,error_code,created_at from public.case_correspondence where case_id=%s
                order by created_at desc limit 30""",
                (case_id,),
            ).fetchall()
        return {
            "workflow": job,
            "correspondence": drafts,
            "content_revision": case.content_revision,
            "insurance_source": {
                "version": "mock-insurance-v2",
                "synthetic": True,
                "fr_count": 1000,
                "uk_count": 1000,
            },
        }


class LegacyMediaWorkflow:
    def __init__(self, repository, media=None, dust=None):
        self.repository = repository
        self.media = media or MediaAnalysisService(repository)
        self.dust = dust or DustService(
            PostgresDustRepository(repository.database_url), DustClient.from_env()
        )

    def received(self, actor_id, case):
        try:
            self.repository.enqueue(UUID(actor_id), case)
        except StaleCaseError:
            # Another receipt has already queued the more recent snapshot atomically.
            pass
        self.tick(case.id)
        return self.repository.get_case(UUID(actor_id), case.id) or case

    def tick(self, case_id=None):
        job = self.repository.claim(case_id)
        if job is None:
            return False
        try:
            self._advance(job)
        except (StaleCaseError, AnalysisInProgressError):
            self.repository.finish(job, "waiting")
        except UrgentHandoffError:
            self.repository.finish(job, "needs_action", error="urgent_human_handoff")
        except DustError as error:
            self.repository.finish(job, "failed", error=error.code)
        except Exception:
            logger.exception("Media workflow failed; stored media retained")
            self.repository.finish(job, "failed", error="workflow_failed")
        return True

    def _advance(self, job):
        actor, case_id = job["actor_id"], job["case_id"]
        case = self.repository.get_case(actor, case_id)
        if case is None or case.content_revision != job["content_revision"]:
            self.repository.finish(job, "stale")
            return
        if case.status in {"registered", "sent"}:
            self.repository.finish(job, "needs_action", error="case_read_only")
            return
        latest = case.latest_analysis
        # Never retry an uncertain/still-running provider call just because a lease expired.
        if (
            latest
            and latest.status == "running"
            and latest.input_content_revision == case.content_revision
        ):
            age = (datetime.now(timezone.utc) - latest.started_at).total_seconds()
            self.repository.finish(
                job,
                "waiting" if age < 360 else "needs_action",
                error="analysis_in_progress",
            )
            return
        # A new generation is created only by an explicit retry of a failed job.
        # It may retry failed Gemini; ready results remain cached across generations.
        result = self.media.run(
            str(actor),
            case_id,
            AnalysisRunRequest(expected_state_version=case.state_version),
            automatic=job["generation"] == 1 and job.get("attempts", 1) <= 1,
        )
        case = result.case
        if result.analysis_run.input_content_revision != case.content_revision:
            self.repository.finish(job, "stale")
            return
        if result.analysis_run.status != "ready":
            error = result.analysis_run.error_code or "media_analysis_failed"
            retryable = error in {"gemini_rate_limited", "gemini_temporarily_unavailable", "gemini_unavailable"}
            attempt = job.get("attempts", 1)
            self.repository.finish(
                job, "waiting" if retryable and attempt < 3 else "failed",
                error=error, delay_seconds=min(60 * attempt, 180),
            )
            return
        matches = lookup_media_plates(case)
        self.repository.checkpoint(job, matches, result.analysis_run.id)
        # Dust continues asynchronously; the durable job polls with a fresh lease.
        key = uuid5(
            NAMESPACE_URL,
            f"claimroom:garage:{case_id}:{case.content_revision}:{job['generation']}",
        )
        existing = self.dust.repository.find_request(actor, case_id, key)
        if existing:
            try:
                run = self.dust.refresh(str(actor), case_id, existing["id"])
            except DustError as error:
                temporary = error.code in {
                    "dust_rate_limited",
                    "dust_unavailable",
                    "dust_request_failed",
                }
                self.repository.finish(
                    job,
                    "waiting" if temporary else "failed",
                    garage_id=existing["id"],
                    error=error.code,
                    delay_seconds=60,
                )
                return
        else:
            run = self.dust.start(
                str(actor),
                case_id,
                DustRunRequest(
                    agent="garage",
                    expected_state_version=case.state_version,
                    idempotency_key=key,
                ),
            )
        status = {
            "ready": "ready",
            "failed": "failed",
            "submission_unknown": "needs_action",
            "needs_action": "needs_action",
            "stale": "stale",
        }.get(run.status, "waiting")
        if run.status == "ready":
            if not run.output or not run.output.draft_body:
                status = "failed"
            else:
                self.repository.save_garage_draft(actor, case, run)
        self.repository.finish(
            job,
            status,
            garage_id=run.id,
            error=run.error_code
            or (
                "garage_draft_missing"
                if status == "failed" and run.status == "ready"
                else None
            ),
        )


class MediaWorkflow:
    """Active flow: Astra, one handler decision, then notifications. No Dust or city request."""
    def __init__(self, repository, journey=None):
        from claim_api.accident_journey import AccidentJourney
        self.repository = repository
        self.journey = journey or AccidentJourney(repository)

    def received(self, actor_id, case):
        try:
            self.repository.enqueue(UUID(actor_id), case)
        except StaleCaseError:
            pass
        # Receipts return promptly; the durable worker owns model calls.
        return self.repository.get_case(UUID(actor_id), case.id) or case

    def tick(self, case_id=None):
        from claim_api.analysis import AnalysisError
        delivered = self.journey.deliver(case_id)
        if case_id is None:
            self._recover_deposit_invitations()
        job = self.repository.claim(case_id)
        if job is None:
            return delivered
        try:
            self.journey.analyze(job)
        except AnalysisError as error:
            if error.code == 'astra_analysis_in_progress':
                self.repository.finish(job, 'waiting', error=error.code, delay_seconds=30)
                return True
            retryable = error.code in {'astra_rate_limited','astra_unavailable','astra_incomplete_response'}
            attempt = job.get('attempts', 1)
            self.repository.finish(job, 'waiting' if retryable and attempt < 3 else 'failed',
                                   error=error.code, delay_seconds=min(30 * attempt, 120))
        except Exception:
            logger.exception('Automatic accident analysis failed')
            self.repository.finish(job, 'failed', error='workflow_failed')
        return True

    def _recover_deposit_invitations(self):
        """Recover an ended call if its webhook was interrupted before SMS creation."""
        import os
        if os.getenv('SMS_LINK_DELIVERY_MODE','disabled') != 'twilio':
            return
        from claim_api.routes.voice import _auto_fake_follow_up
        with self.repository._connection() as conn:
            rows = conn.execute("""select c.id,c.created_by_user_id from public.voice_sessions v
                join public.cases c on c.id=v.case_id where v.mode='live' and v.telephony_provider='twilio'
                and v.triage_json->>'status' in ('complete','incomplete')
                and exists(select 1 from public.voice_session_events e where e.session_id=v.id
                           and e.event_type='end_of_call_report')
                and v.updated_at>now()-interval '30 minutes'
                and not exists(select 1 from public.case_messages m where m.case_id=c.id and m.channel='sms'
                    and m.provider='twilio.sms.claimroom') order by v.updated_at limit 5""").fetchall()
        for row in rows:
            _auto_fake_follow_up(row['created_by_user_id'],row['id'])
