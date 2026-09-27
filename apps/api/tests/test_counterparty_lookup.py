"""S14 N01–N06: source association, exact mock outcomes and current result contract."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from claim_api.analysis import correspondent_query_matches
from claim_api.case_service import CaseService
from claim_api.counterparty_lookup import (
    CounterpartyLookupRequest, CounterpartyLookupService, LookupRejectedError, _check_observations,
    _compatible_partial, canonical_query, current_result_ids,
)
from claim_api.fixtures import get_scenario
from claim_api.mock_insurance import SOURCE_VERSION, correspondent_lookup, insurance_lookup
from claim_api.models import AnalysisSourceRef, CaseView, IntakePatch, ProviderResultView
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.video_reuse import configured_vision_pipeline

from test_alembic_version import _apply_alembic, _test_database_url


DAY = date(2026, 9, 25)
EVIDENCE = uuid4()
PLATE_REF = AnalysisSourceRef(kind="evidence", id=str(EVIDENCE), locator="video:3200-4200")
MOVEMENT_REF = AnalysisSourceRef(kind="evidence", id=str(EVIDENCE), locator="video:1200-3200")


class ObservationCursor:
    def __init__(self, observations):
        self.observations = observations
        self.count = 0

    def execute(self, *_args):
        self.count += 1

    def fetchall(self):
        if self.count == 1:
            return []
        return [{"evidence_id": EVIDENCE, "observations_json": self.observations}]


def request(plate="AB12 CDE", track="dark-car", status="human_confirmed", refs=None):
    return CounterpartyLookupRequest(
        plate_candidate=plate, country="UK", incident_date=DAY,
        vehicle_track_id=track, identification_status=status,
        validation_reason="Handler checked the full plate against the original recording" if status == "human_confirmed" else None,
        supporting_source_refs=refs or [PLATE_REF, MOVEMENT_REF], expected_state_version=1,
    )


def g1_observations():
    return [
        {"category": "movement", "status": "observed", "start_ms": 1200, "end_ms": 3200,
         "vehicle_track_id": "dark-car"},
        {"category": "visible_text", "status": "uncertain", "start_ms": 3200, "end_ms": 4200,
         "vehicle_track_id": "dark-car", "plate_candidate": "AB12 CD?", "uncertain_positions": [7]},
    ]


def test_n01_confirmed_bmw_plate_uses_exact_mock_insurer_and_correspondent():
    _check_observations(ObservationCursor(g1_observations()), uuid4(), request(), "AB12CDE")
    insurance = insurance_lookup("AB12 CDE", "UK", DAY)
    correspondent = correspondent_lookup(insurance.data["insurer_id"], "FR", DAY)
    assert insurance.status == correspondent.status == "matched"
    assert insurance.data["insurer_name"] == "Northbridge Demo Motor (fictional)"
    assert correspondent.data["correspondent_name"] == "Hexagone Demo Recours (fictional)"


def test_n02_partial_plate_is_not_resolved_from_registry():
    assert insurance_lookup("AB12 CD?", "UK", DAY).status == "ambiguous"
    _check_observations(ObservationCursor(g1_observations()), uuid4(),
                        request("AB12 CD?", status="uncertain"), "AB12CD?")
    assert not _compatible_partial("AB12 XD?", "AB12CDE")
    with pytest.raises(LookupRejectedError, match="full plate"):
        _check_observations(ObservationCursor(g1_observations()), uuid4(),
                            request(status="observed"), "AB12CDE")


def test_n03_blue_uninvolved_car_cannot_be_selected_even_with_visible_plate():
    observations = [
        {"category": "visible_text", "status": "observed", "start_ms": 3200, "end_ms": 4200,
         "vehicle_track_id": "blue-car", "plate_candidate": "XY34 ZTR"},
        {"category": "movement", "status": "observed", "start_ms": 1200, "end_ms": 3200,
         "vehicle_track_id": "dark-car"},
    ]
    with pytest.raises(LookupRejectedError):
        _check_observations(ObservationCursor(observations), uuid4(),
                            request("XY34 ZTR", "blue-car", "observed"), "XY34ZTR")


def test_n04_no_match_expired_future_and_no_correspondent_keep_exact_status():
    assert insurance_lookup("ZZ99 ZZZ", "UK", DAY).reason == "plate_not_found"
    assert insurance_lookup("AB12 CDE", "UK", date(2027, 1, 1)).reason == "coverage_expired"
    assert insurance_lookup("AB12 CDE", "UK", date(2025, 12, 31)).reason == "coverage_not_started"
    assert correspondent_lookup("DEMO-UK-INS-00", "FR", date(2025, 1, 1)).status == "unavailable"
    no_correspondent = insurance_lookup("AB16 CDE", "UK", DAY)
    assert no_correspondent.status == "matched"
    assert correspondent_lookup(no_correspondent.data["insurer_id"], "FR", DAY).status == "no_match"


def test_n05_correspondent_contract_requires_same_insurer_date_and_result_id():
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    insurance = insurance_lookup("AB12 CDE", "UK", DAY)
    insurance_id = uuid4()
    a = ProviderResultView(id=insurance_id, source_id=insurance_id, provider="insurance_lookup", mode="mock",
                           status="matched", source_version=SOURCE_VERSION, query_hash="x", query=insurance.query,
                           retrieved_at=now, data=insurance.data)
    correspondent = correspondent_lookup(insurance.data["insurer_id"], "FR", DAY)
    b_id = uuid4()
    b = ProviderResultView(id=b_id, source_id=b_id, provider="correspondent_lookup", mode="mock",
                           status="matched", source_version=SOURCE_VERSION, query_hash="y",
                           query={**correspondent.query, "insurance_result_id": str(insurance_id)},
                           retrieved_at=now, data=correspondent.data)
    assert correspondent_query_matches(a, b)
    assert not correspondent_query_matches(a, b.model_copy(update={"query": {**b.query, "incident_date": "2026-09-26"}}))
    assert not correspondent_query_matches(a, b.model_copy(update={"query": {**b.query, "insurance_result_id": str(uuid4())}}))


def test_n06_canonical_query_deduplicates_order_and_stale_selection_has_no_current_results():
    assert canonical_query({"plate": "AB12CDE", "date": "2026-09-25"})[1] == canonical_query(
        {"date": "2026-09-25", "plate": "AB12CDE"})[1]
    fixture = get_scenario("g1")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    case_id = uuid4()
    from claim_api.models import CounterpartyLookupView
    current = CounterpartyLookupView(
        plate_candidate="AB12CDE", country="UK", incident_date=DAY, vehicle_track_id="dark-car",
        identification_status="human_confirmed", supporting_source_refs=[PLATE_REF, MOVEMENT_REF],
        validation_reason="checked", validated_by_user_id=uuid4(), validated_at=now,
        insurance_result_id=uuid4(), current=False,
    )
    case = CaseView(id=case_id, created_by_user_id=uuid4(), synthetic=True, status="collecting",
                    state_version=2, content_revision=2, scenario_id="g1", created_at=now, updated_at=now,
                    intake=fixture.intake, counterparty_lookup=current)
    assert current_result_ids(case) == set()


def test_lookup_persists_once_then_invalidates_on_accident_date_change(monkeypatch):
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    repository = PostgresCaseRepository(database_url)
    service = CounterpartyLookupService(repository)
    actor, evidence_id = uuid4(), uuid4()
    with repository._connection() as connection, connection.cursor() as cursor:
        cursor.execute("insert into auth.users(id) values (%s)", (actor,))
    created = CaseService(repository).create_case(str(actor), "g1")
    plate_ref = AnalysisSourceRef(kind="evidence", id=str(evidence_id), locator="video:3200-4200")
    movement_ref = AnalysisSourceRef(kind="evidence", id=str(evidence_id), locator="video:1200-3200")
    observations = [
        {**item, "category": "plate" if item["category"] == "visible_text" else item["category"],
         "source_ref": {"kind": "evidence", "id": str(evidence_id),
                                 "locator": f"video:{item['start_ms']}-{item['end_ms']}"}}
        for item in g1_observations()
    ]
    with repository._connection() as connection, connection.cursor() as cursor:
        cursor.execute("""insert into public.evidence
            (id,case_id,storage_path,kind,source_kind,mode,mime_type,byte_size,checksum_status)
            values (%s,%s,%s,'video','handler_upload','mock','video/mp4',42,'client_declared')""",
            (evidence_id, created.id, f"{created.id}/{evidence_id}.mp4"))
        cursor.execute("""insert into public.provider_results
            (case_id,provider,mode,status,query_hash,source_version,query_json,payload_json)
            values (%s,'vision','mock','matched',%s,'vision-observation-v1','{}',%s)""",
            (created.id, "v" * 64, Jsonb({"pipeline_fingerprint": configured_vision_pipeline().fingerprint,
                                         "observations": observations})))

    first_request = CounterpartyLookupRequest(
        plate_candidate="AB12 CDE", country="UK", incident_date=DAY,
        vehicle_track_id="dark-car", identification_status="human_confirmed",
        validation_reason="Checked the uncertain final character against the original recording",
        supporting_source_refs=[plate_ref, movement_ref], expected_state_version=created.state_version,
    )
    first = service.run(str(actor), created.id, first_request)
    assert first.counterparty_lookup is not None and first.counterparty_lookup.current
    assert first.counterparty_lookup.validated_by_user_id == actor
    assert first.counterparty_lookup.validated_at is not None
    assert first.counterparty_lookup.correspondent_result_id is not None
    assert current_result_ids(first) == {
        first.counterparty_lookup.insurance_result_id,
        first.counterparty_lookup.correspondent_result_id,
    }
    repeated = service.run(str(actor), created.id,
                           first_request.model_copy(update={"expected_state_version": first.state_version}))
    assert repeated.state_version == first.state_version
    assert repeated.content_revision == first.content_revision
    assert len(repeated.provider_results) == len(first.provider_results)

    corrected = CaseService(repository).update_intake(str(actor), created.id, first.state_version,
        IntakePatch(incident_at=datetime(2026, 9, 26, 10, 33, tzinfo=timezone(timedelta(hours=2)))))
    assert corrected.counterparty_lookup is not None and not corrected.counterparty_lookup.current
    assert current_result_ids(corrected) == set()
    with pytest.raises(LookupRejectedError) as error:
        service.run(str(actor), created.id,
                    first_request.model_copy(update={"expected_state_version": corrected.state_version}))
    assert error.value.code == "incident_date_mismatch"

    refreshed = service.run(str(actor), created.id, first_request.model_copy(update={
        "incident_date": date(2026, 9, 26), "expected_state_version": corrected.state_version,
    }))
    assert refreshed.counterparty_lookup is not None and refreshed.counterparty_lookup.current
    assert refreshed.counterparty_lookup.insurance_result_id != first.counterparty_lookup.insurance_result_id
    assert len(refreshed.provider_results) == len(first.provider_results) + 2
