from __future__ import annotations

import base64
import hashlib
import hmac
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlencode
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.case_service import CaseService
from claim_api.garage_worker import process_one
from claim_api.garage_repository import GarageRepository, enqueue_photo_reply
from claim_api.garages import GarageError, OsmGarageFinder, Point
from claim_api.main import create_app
from claim_api.routes.cases import get_case_service
from claim_api.routes.garages import SmsSettingsRequest, get_garage_finder, get_garage_repository
from claim_api.twilio_sms import SmsError, TwilioSms, delivery_status
from test_cases_api import MemoryCaseRepository


ORIGIN = Point(latitude=50.63, longitude=3.06)
ACCOUNT = "AC" + "a" * 32
MESSAGE = "SM" + "b" * 32


def garage(id, metres, name="Garage test", kind="node"):
    coordinates = {"lat": ORIGIN.latitude + metres / 111195, "lon": ORIGIN.longitude}
    return {"type": kind, "id": id, **(coordinates if kind == "node" else {"center": coordinates}),
            "tags": {"shop": "car_repair", "name": name}}


def finder_with(elements):
    calls = []
    def open_url(request, timeout):
        calls.append(request)
        return BytesIO(json.dumps({"elements": elements}).encode())
    return OsmGarageFinder(open_url=open_url), calls


@pytest.mark.parametrize("metres,radius", [(200, 500), (750, 1000), (1400, 2000), (3500, 5000)])
def test_expanding_radius_returns_first_nonempty_circle(metres, radius):
    finder, calls = finder_with([garage(1, metres), garage(2, 4500, "Farther")])
    result = finder.preview(None, ORIGIN)
    assert result.status == "ready"
    assert result.radius_m == radius
    assert result.garages[0].distance_m == metres
    assert len(calls) == 1
    assert 'nwr["shop"="car_repair"]' in parse_qs(calls[0].data.decode())["data"][0]
    finder.preview(None, ORIGIN)
    assert len(calls) == 1  # Cached repeat is not another public OSM request.


def test_sorted_limited_deduplicated_garages_and_navigation_coordinates():
    rows = [garage(1, 350, "C"), garage(2, 50, "A"), garage(3, 55, "A", "way"),
            garage(4, 240, "B", "relation"), garage(5, 400, "D"), garage(6, 700, "E")]
    finder, _ = finder_with(rows)
    result = finder.preview(None, ORIGIN)
    assert [item.name for item in result.garages] == ["A", "B", "C"]
    assert [item.distance_m for item in result.garages] == [50, 240, 350]
    params = parse_qs(urlsplit(result.garages[0].directions_url).query)
    assert params["api"] == ["1"] and params["travelmode"] == ["driving"]
    assert "origin" not in params and params["destination"][0].endswith(",3.060000")
    assert "vol d'oiseau" in result.sms_body and "openstreetmap.org/copyright" in result.sms_body


def test_empty_results_differ_from_provider_error_and_invalid_elements_are_ignored():
    finder, _ = finder_with([garage(1, 5100), {"type": "node", "tags": {"shop": "car_repair"}},
                            {"type": "node", "id": 1, "lat": 50.63, "lon": 3.06, "tags": {"shop": "car_parts"}}])
    result = finder.preview(None, ORIGIN)
    assert result.status == "no_results" and result.radius_m == 5000
    assert "Aucun garage référencé" in result.sms_body
    failing = OsmGarageFinder(open_url=lambda *a, **k: BytesIO(b'{"elements":[],"remark":"runtime timeout"}'))
    with pytest.raises(GarageError, match="osm_invalid_response"):
        failing.preview(None, ORIGIN)
    with pytest.raises(ValueError):
        Point(latitude=float("nan"), longitude=3)


@pytest.mark.parametrize("broad,ambiguous", [(True, False), (False, True)])
def test_ambiguous_or_town_level_location_never_chooses_an_arbitrary_origin(broad, ambiguous):
    row = {"lat": "50.63", "lon": "3.06", "display_name": "Test address",
           "boundingbox": ["50.62", "50.65", "3.0", "3.2"] if broad else ["50.63", "50.631", "3.06", "3.061"]}
    rows = [row, {**row, "lon": "3.061", "display_name": "Another address"}] if ambiguous else [row]
    finder = OsmGarageFinder(open_url=lambda *a, **k: BytesIO(json.dumps(rows).encode()))
    result = finder.preview("Rue de la Gare")
    assert result.status == "needs_location" and result.sms_body is None
    assert result.location_candidates[0].precise is not broad
    assert finder.preview(None).status == "needs_location"


def configure_twilio(monkeypatch):
    monkeypatch.setenv("GARAGE_SMS_TRIGGER", "mms")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", ACCOUNT)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15005550006")
    monkeypatch.setenv("TWILIO_PUBLIC_BASE_URL", "https://claims.example.test")
    monkeypatch.delenv("TWILIO_MESSAGING_SERVICE_SID", raising=False)
    monkeypatch.delenv("TWILIO_SMS_MESSAGING_SERVICE_SID", raising=False)


def sign(path, fields):
    data = "https://claims.example.test" + path + "".join(k + str(fields[k]) for k in sorted(fields))
    return base64.b64encode(hmac.new(b"test-token", data.encode(), hashlib.sha1).digest()).decode()


def test_twilio_send_builds_sms_and_treats_timeouts_as_uncertain(monkeypatch):
    configure_twilio(monkeypatch)
    calls = []
    def open_url(request, timeout):
        calls.append(request)
        return BytesIO(json.dumps({"sid": MESSAGE}).encode())
    client = TwilioSms(open_url=open_url)
    message_id = str(uuid4())
    assert client.send("+33600000000", "Garage et itinéraire", message_id) == MESSAGE
    fields = parse_qs(calls[0].data.decode())
    assert fields["To"] == ["+33600000000"] and "MediaUrl" not in fields
    assert fields["StatusCallback"][0].endswith(message_id)
    def timed_out(*args, **kwargs):
        raise TimeoutError
    client.open_url = timed_out
    with pytest.raises(SmsError) as exc:
        client.send("+33600000000", "body", message_id)
    assert exc.value.uncertain is True


def test_twilio_reuses_existing_messaging_service_environment_without_a_from_number(monkeypatch):
    configure_twilio(monkeypatch)
    monkeypatch.delenv("TWILIO_PHONE_NUMBER")
    monkeypatch.delenv("TWILIO_PUBLIC_BASE_URL")
    monkeypatch.setenv("TWILIO_SMS_MESSAGING_SERVICE_SID", "MG" + "c" * 32)
    monkeypatch.setenv("TWILIO_SMS_WEBHOOK_BASE_URL", "https://claims.example.test/v1/webhooks/twilio/sms")
    requests = []
    def open_url(request, timeout):
        requests.append(request)
        return BytesIO(json.dumps({"sid": MESSAGE}).encode())
    TwilioSms(open_url=open_url).send("+33600000000", "Test", str(uuid4()))
    fields = parse_qs(requests[0].data.decode())
    assert fields["MessagingServiceSid"] == ["MG" + "c" * 32] and "From" not in fields


@pytest.mark.parametrize("current,incoming,expected", [
    ("delivered", "sent", "delivered"), ("sent", "queued", "sent"),
    ("accepted", "undelivered", "failed"), ("unknown", "delivered", "delivered"),
    ("failed", "queued", "failed"), ("failed", "delivered", "delivered"),
])
def test_delivery_callbacks_do_not_regress(current, incoming, expected):
    assert delivery_status(current, incoming) == expected


def test_webhook_rejects_invalid_signature_and_ignores_plain_sms(monkeypatch):
    configure_twilio(monkeypatch)
    class Repository:
        def __init__(self): self.calls = []
        def enqueue_mms(self, *args): self.calls.append(args)
    repository = Repository()
    app = create_app()
    app.dependency_overrides[get_garage_repository] = lambda: repository
    client = TestClient(app)
    path = "/v1/webhooks/twilio/inbound"
    fields = {"AccountSid": ACCOUNT, "MessageSid": MESSAGE, "From": "+33600000000", "To": "+15005550006", "NumMedia": "0"}
    assert client.post(path, data=fields).status_code == 403
    headers = {"X-Twilio-Signature": sign(path, fields)}
    assert client.post(path, data=fields, headers=headers).status_code == 200
    assert repository.calls == []
    assert client.post(path, data={**fields, "From": "+33700000000"}, headers=headers).status_code == 403
    fields.update(NumMedia="1", MediaContentType0="image/jpeg",
                  MediaUrl0=f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT}/Messages/{MESSAGE}/Media/ME{'c'*32}")
    response = client.post(path, data=fields, headers={"X-Twilio-Signature": sign(path, fields)})
    assert response.status_code == 200 and response.text == "<Response/>"
    assert repository.calls[0][0] == "+33600000000"
    fields["MediaUrl0"] = "http://127.0.0.1/private"
    assert client.post(path, data=fields, headers={"X-Twilio-Signature": sign(path, fields)}).status_code == 400


def test_preview_enforces_case_ownership_version_and_valid_coordinates():
    actor = uuid4()
    repo = MemoryCaseRepository()
    service = CaseService(repo)
    case = service.create_case(str(actor), "complete")
    finder, calls = finder_with([garage(1, 100)])
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(actor))
    app.dependency_overrides[get_case_service] = lambda: service
    app.dependency_overrides[get_garage_finder] = lambda: finder
    client = TestClient(app)
    path = f"/v1/cases/{case.id}/garage-preview"
    body = {"expected_state_version": 1, "origin": ORIGIN.model_dump()}
    assert client.post(path, json=body).status_code == 200
    assert client.post(path, json={**body, "expected_state_version": 2}).status_code == 409
    assert client.post(path, json={**body, "origin": {"latitude": 100, "longitude": 3}}).status_code == 422
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(uuid4()))
    assert client.post(path, json=body).status_code == 404
    assert len(calls) == 1


class WorkerRepo:
    def __init__(self, mode="mock", origin=True):
        self.job = {"id": uuid4(), "mode": mode, "origin_json": ORIGIN.model_dump() if origin else None,
                    "location_text": None, "recipient": "+33600000000"}
        self.finishes = []
        self.prepared = []
    def claim_next(self):
        job, self.job = self.job, None
        return job
    def prepare(self, job, preview):
        self.prepared.append(preview)
        return uuid4()
    def finish(self, job_id, **values): self.finishes.append(values)


def test_worker_mock_never_calls_twilio_and_missing_location_never_sends():
    class NeverSend:
        def send(self, *a): raise AssertionError("No SMS should be sent")
    finder, _ = finder_with([garage(1, 100)])
    repository = WorkerRepo()
    assert process_one(repository, finder, NeverSend()) is True
    assert repository.finishes[-1] == {"status": "done", "sid": None}
    assert process_one(repository, finder, NeverSend()) is False
    missing = WorkerRepo(origin=False)
    process_one(missing, finder, NeverSend())
    assert missing.finishes[-1]["status"] == "needs_location" and missing.prepared == []


def test_worker_does_not_retry_an_uncertain_submission(monkeypatch):
    monkeypatch.setenv("GARAGE_SMS_MODE", "live")
    class Transport:
        def check_configuration(self): pass
        def send(self, *a): raise SmsError("twilio_submission_unknown", uncertain=True)
    finder, _ = finder_with([garage(1, 100)])
    repository = WorkerRepo(mode="live")
    process_one(repository, finder, Transport())
    assert repository.finishes[-1]["status"] == "unknown"
    assert process_one(repository, finder, Transport()) is False


def test_switching_back_to_mock_cancels_queued_live_jobs(monkeypatch):
    monkeypatch.setenv("GARAGE_SMS_MODE", "mock")
    repository = WorkerRepo(mode="live")
    process_one(repository, None, None)
    assert repository.finishes == [{"status": "cancelled", "error": "live_sms_disabled"}]


def test_web_upload_does_not_trigger_a_reply_when_mms_is_selected(monkeypatch):
    monkeypatch.setenv("GARAGE_SMS_TRIGGER", "mms")
    enqueue_photo_reply(None, None, None, "damage_photo", "image/jpeg")


def test_migration_renders_durable_queue_and_private_access(monkeypatch):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://unused:unused@localhost/claim_test?sslmode=require")
    output = StringIO()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "20260926_reference_data:20260927_garage_sms", sql=True)
    sql = output.getvalue()
    assert "garage_reply_jobs" in sql and "trigger_id text not null unique" in sql
    assert "revoke all on public.garage_reply_jobs from public" in sql
    assert "rolname in ('anon','authenticated')" in sql
    assert "revoke all on public.garage_reply_jobs from %I" in sql


@pytest.fixture
def garage_db(monkeypatch):
    import psycopg
    from psycopg.rows import dict_row
    from test_alembic_version import _apply_alembic, _test_database_url
    url = _test_database_url()
    _apply_alembic(url, monkeypatch)
    monkeypatch.setenv("GARAGE_SMS_MODE", "mock")
    monkeypatch.setenv("GARAGE_SMS_TRIGGER", "photos")
    with psycopg.connect(url, row_factory=dict_row) as connection:
        with connection.transaction(force_rollback=True):
            # Earlier portal tests also enqueue real outbox rows. Isolate the
            # global worker/routing fixture; force_rollback restores those rows.
            connection.execute("delete from public.garage_reply_jobs")
            connection.execute("update public.garage_sms_settings set enabled=false")
            class Repository(GarageRepository):
                @contextmanager
                def _connection(self):
                    yield connection
            repository = Repository(url)
            actor = uuid4()
            connection.execute("insert into auth.users(id) values (%s)", (actor,))
            case = CaseService(repository).create_case(str(actor), "complete")
            request = SmsSettingsRequest(expected_state_version=case.state_version,
                recipient="+33600000000", enabled=True, origin=ORIGIN)
            repository.configure(actor, case.id, request)
            yield repository, connection, actor, case, request


def test_postgres_photo_enqueue_and_replays_are_atomic_and_idempotent(garage_db):
    repository, conn, actor, case, request = garage_db
    path = f"{case.id}/fixture-photo.jpg"
    repository.register_upload_intent(actor, case.id, case.state_version, path, "scene_photo", "image/jpeg",
        100, "a" * 64, datetime.now(timezone.utc) + timedelta(hours=1))
    saved = repository.finalize_evidence(actor, case.id, path, case.state_version)
    repository.finalize_evidence(actor, case.id, path, case.state_version)
    second_path = f"{case.id}/second-photo.jpg"
    repository.register_upload_intent(actor, case.id, saved.state_version, second_path, "damage_photo", "image/jpeg",
        100, "b" * 64, datetime.now(timezone.utc) + timedelta(hours=1))
    saved = repository.finalize_evidence(actor, case.id, second_path, saved.state_version)
    repository.enqueue_existing_photos(actor, case.id)
    # Saving identical settings must not create a second batch.
    repository.configure(actor, case.id, request.model_copy(update={"expected_state_version": saved.state_version}))
    repository.enqueue_existing_photos(actor, case.id)
    rows = conn.execute("select * from public.garage_reply_jobs where case_id=%s", (case.id,)).fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "queued"
    finder, _ = finder_with([garage(1, 100)])
    process_one(repository, finder, None)
    result = repository.settings(actor, case.id)
    assert result["jobs"][0]["status"] == "done"
    assert result["jobs"][0]["delivery_status"] == "accepted"
    message = conn.execute("select channel from public.case_messages where id=%s",
        (conn.execute("select message_id from public.garage_reply_jobs where case_id=%s", (case.id,)).fetchone()["message_id"],)).fetchone()
    assert message["channel"] == "sms"
    assert CaseService(repository).get_case(str(actor), case.id).state_version == saved.state_version


def test_postgres_mms_deduplication_and_early_out_of_order_callbacks(garage_db, monkeypatch):
    monkeypatch.setenv("GARAGE_SMS_TRIGGER", "mms")
    repository, conn, actor, case, request = garage_db
    repository.enqueue_mms(request.recipient, MESSAGE, [{"mime_type": "image/jpeg"}])
    repository.enqueue_mms(request.recipient, MESSAGE, [{"mime_type": "image/jpeg"}])
    job = repository.claim_next()
    assert repository.claim_next() is None
    conn.execute("update public.garage_reply_jobs set mode='live' where id=%s", (job["id"],))
    job["mode"] = "live"
    finder, _ = finder_with([garage(1, 100)])
    message_id = repository.prepare(job, finder.preview(None, ORIGIN))
    repository.callback(message_id, MESSAGE, "delivered", request.recipient, None)
    repository.finish(job["id"], status="done", sid=MESSAGE)
    repository.callback(message_id, MESSAGE, "queued", request.recipient, None)
    message = conn.execute("select * from public.case_messages where id=%s", (message_id,)).fetchone()
    assert message["status"] == "delivered" and message["sent_at"] <= message["delivered_at"]
    assert message["provider_message_id"] == MESSAGE


def test_depot_automatically_replies_to_exact_invitation_recipient(garage_db):
    repository, conn, actor, case, request = garage_db
    conn.execute("delete from public.garage_sms_settings where case_id=%s", (case.id,))
    invitation_id = uuid4()
    conn.execute("""insert into public.case_messages
        (id,case_id,channel,recipient,body,provider,idempotency_key,payload_hash)
        values (%s,%s,'sms','+33700000000','Invitation au dépôt','fixture',%s,%s)""",
        (invitation_id, case.id, str(uuid4()), "a" * 64))
    grant_id = conn.execute("""insert into public.deposit_grants
        (case_id,token_sha256,capabilities,content_revision,expires_at,message_id)
        values (%s,%s,array['upload_evidence'],%s,now()+interval '1 hour',%s) returning id""",
        (case.id, hashlib.sha256(str(uuid4()).encode()).hexdigest(), case.content_revision, invitation_id)).fetchone()["id"]
    # A newer message to someone else must never change this deposit's recipient.
    conn.execute("""insert into public.case_messages
        (case_id,recipient,body,provider,idempotency_key,payload_hash)
        values (%s,'+33600000000','Autre message','fixture',%s,%s)""", (case.id, str(uuid4()), "b" * 64))
    path = f"{case.id}/insured-photo.jpg"
    repository.register_upload_intent(actor, case.id, case.state_version, path, "damage_photo", "image/jpeg",
        100, "a" * 64, datetime.now(timezone.utc) + timedelta(hours=1),
        source_kind="insured_upload", deposit_grant_id=grant_id)
    repository.finalize_evidence(actor, case.id, path, case.state_version, "a" * 64)
    repository.finalize_evidence(actor, case.id, path, case.state_version, "a" * 64)
    jobs = conn.execute("select * from public.garage_reply_jobs where case_id=%s", (case.id,)).fetchall()
    assert len(jobs) == 1 and jobs[0]["recipient"] == "+33700000000"
    assert repository.settings(actor, case.id)["settings"]["enabled"] is True


def test_explicit_disable_prevents_auto_activation_from_deposit(garage_db):
    repository, conn, actor, case, request = garage_db
    repository.configure(actor, case.id, request.model_copy(update={"enabled": False}))
    row = conn.execute("select * from public.cases where id=%s", (case.id,)).fetchone()
    enqueue_photo_reply(conn, row, uuid4(), "damage_photo", "image/jpeg", deposit_grant_id=uuid4())
    assert conn.execute("select count(*) as n from public.garage_reply_jobs where case_id=%s", (case.id,)).fetchone()["n"] == 0


def test_overpass_timeout_falls_back_to_real_nearby_pois_and_caches(monkeypatch):
    calls = []
    row = {"osm_type": "way", "osm_id": 123, "lat": ORIGIN.latitude + .002, "lon": ORIGIN.longitude,
           "category": "shop", "type": "car_repair", "name": "Garage de secours",
           "address": {"house_number": "12", "road": "Rue Test", "city": "Lille", "postcode": "59000"}}
    def open_url(request, timeout):
        calls.append((request, timeout))
        if request.data:
            raise TimeoutError()
        return BytesIO(json.dumps([row, {**row, "osm_id": 124, "type": "car_parts"},
                                  {**row, "osm_id": 125, "lat": 0}, {**row, "lat": "invalid"}]).encode())
    finder = OsmGarageFinder(open_url=open_url)
    result = finder.preview(None, ORIGIN)
    assert result.status == "ready"
    assert [(g.name, g.osm_id) for g in result.garages] == [("Garage de secours", "way/123")]
    assert result.garages[0].address == "12 Rue Test, 59000 Lille"
    assert result.garages[0].distance_m == 222
    assert [timeout for _, timeout in calls] == [5, 8]
    params = parse_qs(urlsplit(calls[1][0].full_url).query)
    assert params["q"] == ["[car repair]"] and params["bounded"] == ["1"]
    finder.preview(None, ORIGIN)
    assert len(calls) == 2


def test_empty_fallback_after_provider_failure_does_not_claim_no_garages():
    def open_url(request, timeout):
        if request.data:
            return BytesIO(b'{"elements":[],"remark":"runtime timeout"}')
        return BytesIO(b'[]')
    with pytest.raises(GarageError, match="osm_unavailable"):
        OsmGarageFinder(open_url=open_url).preview(None, ORIGIN)
