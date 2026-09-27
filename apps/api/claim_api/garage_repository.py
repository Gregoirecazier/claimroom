"""Transactional outbox for garage replies. Never performs network I/O."""

from __future__ import annotations

import hashlib
import os
from uuid import uuid4

from psycopg.types.json import Jsonb

from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.garages import GarageError
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.twilio_sms import delivery_status


def delivery_mode():
    mode = os.getenv("GARAGE_SMS_MODE", "mock")
    if mode not in {"mock", "live"}:
        raise GarageError("invalid_sms_mode")
    return mode


def trigger_source():
    source = os.getenv("GARAGE_SMS_TRIGGER", "photos")
    if source not in {"mms", "photos"}:
        raise GarageError("invalid_sms_trigger")
    return source


def enqueue_photo_reply(conn, case, evidence_id, kind, mime_type, *, manual=False, deposit_grant_id=None):
    if not manual and trigger_source() != "photos":
        return
    if kind not in {"scene_photo", "damage_photo", "vehicle_photo"} or not mime_type.startswith("image/"):
        return
    settings = conn.execute("select * from public.garage_sms_settings where case_id=%s", (case["id"],)).fetchone()
    if settings is None and deposit_grant_id is not None:
        # Reuse the recipient of the exact private invitation used by /depot.
        # Do not infer a phone number from media, the latest message or a caller
        # on another dossier; a manager's explicit disable remains authoritative.
        invitation = conn.execute("""select m.recipient from public.deposit_grants g
            join public.case_messages m on m.id=g.message_id and m.case_id=g.case_id
            where g.id=%s and g.case_id=%s and g.revoked_at is null and g.expires_at>now()""",
            (deposit_grant_id, case["id"])).fetchone()
        if invitation:
            settings = conn.execute("""insert into public.garage_sms_settings(case_id,recipient,enabled,location_text)
                values (%s,%s,true,%s) on conflict (case_id) do nothing returning *""",
                (case["id"], invitation["recipient"], case["intake_json"].get("location"))).fetchone()
    if settings and settings["enabled"]:
        # One reply for a batch of uploaded photos, including repeated finalize calls.
        trigger = f'photos:{case["id"]}:{settings["updated_at"].isoformat()}:{delivery_mode()}'
        _enqueue(conn, case, settings, trigger, [{"evidence_id": str(evidence_id)}])


def _enqueue(conn, case, settings, trigger, media):
    location = case["intake_json"].get("location")
    origin = settings["origin_json"] if settings["location_text"] == location else None
    return conn.execute(
        """insert into public.garage_reply_jobs
        (case_id,trigger_id,mode,recipient,location_text,origin_json,media_json)
        values (%s,%s,%s,%s,%s,%s,%s) on conflict (trigger_id) do nothing returning id""",
        (case["id"], trigger, delivery_mode(), settings["recipient"], location, Jsonb(origin), Jsonb(media)),
    ).fetchone()


class GarageRepository(PostgresCaseRepository):
    def settings(self, actor, case_id):
        with self._connection() as conn:
            case = self._owned(conn, actor, case_id)
            settings = conn.execute("select * from public.garage_sms_settings where case_id=%s", (case_id,)).fetchone()
            jobs = conn.execute(
                """select j.id,j.status,j.mode,j.error_code,j.created_at,m.status as delivery_status,
                m.body as sms_body from public.garage_reply_jobs j
                left join public.case_messages m on m.id=j.message_id
                where j.case_id=%s order by j.created_at desc limit 10""", (case_id,),
            ).fetchall()
            return {"settings": settings, "jobs": jobs, "mode": delivery_mode(), "trigger": trigger_source(), "location": case["intake_json"].get("location")}

    @staticmethod
    def _owned(conn, actor, case_id):
        case = conn.execute("select * from public.cases where id=%s and created_by_user_id=%s for update", (case_id, actor)).fetchone()
        if case is None:
            raise CaseNotFoundError
        return case

    def configure(self, actor, case_id, request):
        with self._connection() as conn:
            case = self._owned(conn, actor, case_id)
            if case["state_version"] != request.expected_state_version:
                raise StaleCaseError(case["state_version"])
            changed = conn.execute(
                """insert into public.garage_sms_settings(case_id,recipient,enabled,location_text,origin_json)
                values (%s,%s,%s,%s,%s) on conflict (case_id) do update set
                recipient=excluded.recipient,enabled=excluded.enabled,location_text=excluded.location_text,
                origin_json=excluded.origin_json,updated_at=now()
                where (garage_sms_settings.recipient,garage_sms_settings.enabled,garage_sms_settings.location_text,garage_sms_settings.origin_json)
                is distinct from (excluded.recipient,excluded.enabled,excluded.location_text,excluded.origin_json)
                returning case_id""",
                (case_id, request.recipient, request.enabled, case["intake_json"].get("location"),
                 Jsonb(request.origin.model_dump() if request.origin else None)),
            ).fetchone()
            if not changed:
                return
            # Cancelling is safe before submission; uncertain sends stay visible.
            conn.execute("""update public.garage_reply_jobs set status='cancelled',updated_at=now()
                where case_id=%s and status in ('queued','processing')""", (case_id,))
            self._audit(conn, case, "case.garage_sms_configured", {"enabled": request.enabled}, actor)

    def enqueue_existing_photos(self, actor, case_id):
        with self._connection() as conn:
            case = self._owned(conn, actor, case_id)
            if not conn.execute("select 1 from public.garage_sms_settings where case_id=%s and enabled", (case_id,)).fetchone():
                raise GarageError("garage_sms_not_enabled")
            evidence = conn.execute("""select * from public.evidence where case_id=%s
                and kind in ('scene_photo','damage_photo','vehicle_photo') and mime_type like 'image/%%'
                order by received_at limit 1""", (case_id,)).fetchone()
            if not evidence:
                raise GarageError("garage_photos_required")
            enqueue_photo_reply(conn, case, evidence["id"], evidence["kind"], evidence["mime_type"], manual=True)

    def enqueue_mms(self, sender, message_sid, media):
        if trigger_source() != "mms":
            return
        with self._connection() as conn:
            if conn.execute("select 1 from public.garage_reply_jobs where trigger_id=%s", ("mms:" + message_sid,)).fetchone():
                return
            matches = conn.execute("""select c.*,s.recipient,s.origin_json,s.location_text from public.cases c
                join public.garage_sms_settings s on s.case_id=c.id where s.recipient=%s and s.enabled
                for update of c""", (sender,)).fetchall()
            if len(matches) != 1:
                # Never guess which of two active dossiers belongs to a message.
                raise GarageError("mms_case_not_unique")
            case = matches[0]
            _enqueue(conn, case, case, "mms:" + message_sid, media)

    def claim_next(self):
        with self._connection() as conn:
            # No automatic resend after a worker crash; external acceptance may
            # have happened. Signed callbacks can reconcile an uncertain send.
            stale = conn.execute("""select * from public.garage_reply_jobs
                where status in ('processing','submitting') and updated_at < now()-interval '5 minutes'
                order by id limit 100 for update skip locked""").fetchall()
            for job in stale:
                if job["message_id"]:
                    conn.execute("""update public.case_messages set status='unknown',updated_at=now(),error_code='worker_interrupted'
                        where id=%s and status='queued'""", (job["message_id"],))
                conn.execute("""update public.garage_reply_jobs set status=%s,error_code='worker_interrupted',updated_at=now()
                    where id=%s""", ("unknown" if job["status"] == "submitting" else "failed", job["id"]))
            return conn.execute("""update public.garage_reply_jobs set status='processing',updated_at=now()
                where id=(select id from public.garage_reply_jobs where status='queued'
                order by created_at for update skip locked limit 1) returning *""").fetchone()

    def prepare(self, job, preview):
        with self._connection() as conn:
            # Lock case before job, matching configure and evidence ingestion.
            case = conn.execute("select * from public.cases where id=%s for update", (job["case_id"],)).fetchone()
            current = conn.execute("select * from public.garage_reply_jobs where id=%s for update", (job["id"],)).fetchone()
            if current["status"] != "processing":
                return None
            settings = conn.execute("select * from public.garage_sms_settings where case_id=%s", (job["case_id"],)).fetchone()
            if not settings or not settings["enabled"] or settings["recipient"] != job["recipient"] or case["intake_json"].get("location") != job["location_text"]:
                conn.execute("update public.garage_reply_jobs set status='cancelled',updated_at=now() where id=%s", (job["id"],))
                return None
            message_id = uuid4()
            digest = hashlib.sha256((job["recipient"] + "\n" + preview.sms_body).encode()).hexdigest()
            conn.execute("""insert into public.case_messages
                (id,case_id,channel,mode,recipient,body,status,provider,idempotency_key,payload_hash)
                values (%s,%s,'sms',%s,%s,%s,'queued','twilio',%s,%s)""",
                (message_id, job["case_id"], job["mode"], job["recipient"], preview.sms_body, "garage:" + str(job["id"]), digest))
            conn.execute("""update public.garage_reply_jobs set status='submitting',message_id=%s,result_json=%s,updated_at=now()
                where id=%s""", (message_id, Jsonb(preview.model_dump(mode="json")), job["id"]))
            self._audit(conn, case, "case.garage_sms_prepared", {"message_id": str(message_id), "mode": job["mode"]})
            return message_id

    def finish(self, job_id, *, status, error=None, sid=None):
        with self._connection() as conn:
            job = conn.execute("select * from public.garage_reply_jobs where id=%s for update", (job_id,)).fetchone()
            if job["status"] not in {"processing", "submitting"}:
                return  # A callback, cancellation or crash recovery already won.
            conn.execute("update public.garage_reply_jobs set status=%s,error_code=%s,updated_at=now() where id=%s", (status, error, job_id))
            if job["message_id"]:
                conn.execute("""update public.case_messages set status=%s,provider_message_id=coalesce(provider_message_id,%s),
                    error_code=%s,updated_at=now() where id=%s and status='queued'""",
                    ("accepted" if status == "done" else "unknown" if status == "unknown" else "failed", sid, error, job["message_id"]))

    def callback(self, message_id, sid, status, recipient, error):
        with self._connection() as conn:
            # Same lock order as finish/prepare, including a callback arriving
            # before the REST create response has returned to the worker.
            job = conn.execute("select * from public.garage_reply_jobs where message_id=%s for update", (message_id,)).fetchone()
            if not job or job["mode"] != "live":
                return
            message = conn.execute("select * from public.case_messages where id=%s for update", (message_id,)).fetchone()
            if message["recipient"] != recipient or message["provider_message_id"] not in {None, sid}:
                raise GarageError("twilio_callback_mismatch")
            updated = delivery_status(message["status"], status)
            conn.execute("""update public.case_messages set provider_message_id=%s,status=%s,updated_at=now(),
                sent_at=case when %s in ('sent','delivered') then coalesce(sent_at,now()) else sent_at end,
                delivered_at=case when %s='delivered' then coalesce(delivered_at,now()) else delivered_at end,
                error_code=%s where id=%s""",
                (sid, updated, updated, updated, (error or "twilio_delivery_failed") if updated == "failed" else None, message_id))
            conn.execute("""update public.garage_reply_jobs set status=%s,error_code=%s,updated_at=now() where id=%s""",
                ("failed" if updated == "failed" else "done", error if updated == "failed" else None, job["id"]))

    @staticmethod
    def _audit(conn, case, event, metadata, actor=None):
        conn.execute("""insert into public.audit_events
            (case_id,actor_user_id,event_type,state_version_before,state_version_after,
             content_revision_before,content_revision_after,metadata_json)
            values (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (case["id"], actor, event, case["state_version"], case["state_version"],
             case["content_revision"], case["content_revision"], Jsonb(metadata)))
