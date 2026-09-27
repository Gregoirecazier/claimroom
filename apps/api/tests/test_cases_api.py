from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.case_search import normalize_case_search
from claim_api.case_service import CaseService, StaleCaseError, merge_intake
from claim_api.fixtures import ProviderFixture
from claim_api.main import create_app
from claim_api.models import AuditEventView, CaseListItem, CaseListPage, CaseView, Intake, IntakePatch, ProviderResultView
from claim_api.routes.cases import get_case_service


class MemoryCaseRepository:
    """API test adapter; Postgres semantics are covered by the repository contract."""

    def __init__(self) -> None:
        self.cases: dict[UUID, tuple[UUID, CaseView]] = {}

    def create_case(self, actor_id, scenario_id, intake, provider_results, scenario_version, provider_source_version) -> CaseView:
        now = datetime.now(timezone.utc)
        case_id = uuid4()
        results = []
        for fixture in provider_results:
            row_id = uuid4()
            query_hash = hashlib.sha256(json.dumps(fixture.query, sort_keys=True).encode()).hexdigest()
            results.append(ProviderResultView(
                id=row_id,
                source_id=row_id,
                provider=fixture.provider,
                mode="mock",
                status=fixture.status,
                source_version=provider_source_version,
                query_hash=query_hash,
                retrieved_at=now,
                data=fixture.data,
                reason=fixture.reason,
            ))
        timeline = [AuditEventView(
            id=uuid4(),
            actor_user_id=actor_id,
            event_type="case.created",
            state_version_before=0,
            state_version_after=1,
            content_revision_before=0,
            content_revision_after=1,
            metadata={"scenario_id": scenario_id},
            occurred_at=now,
        )]
        case = CaseView(
            id=case_id,
            created_by_user_id=actor_id,
            scenario_id=scenario_id,
            synthetic=True,
            status="collecting",
            state_version=1,
            content_revision=1,
            created_at=now,
            updated_at=now,
            intake=intake,
            provider_results=results,
            timeline=timeline,
        )
        self.cases[case_id] = (actor_id, case)
        return case

    def list_cases(self, actor_id, limit) -> list[CaseListItem]:
        own = [case for owner, case in self.cases.values() if owner == actor_id]
        own.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
        return [CaseListItem(**case.model_dump(include=CaseListItem.model_fields.keys())) for case in own[:limit]]

    def list_cases_page(self, actor_id, query, offset, limit):
        own = self.list_cases(actor_id, len(self.cases))
        if query:
            own = [case for case in own if normalize_case_search(query) in normalize_case_search(' '.join([
                str(case.id), *[str(getattr(case.intake, field) or '') for field in
                               ('insured_reference', 'insured_name', 'insured_plate', 'insured_vehicle', 'location')]
            ]))]
        return CaseListPage(items=own[offset:offset + limit], total=len(own), offset=offset,
                            limit=limit, has_more=offset + limit < len(own))

    def get_case(self, actor_id, case_id) -> CaseView | None:
        stored = self.cases.get(case_id)
        return stored[1] if stored and stored[0] == actor_id else None

    def delete_case(self, actor_id, case_id) -> bool:
        if self.get_case(actor_id, case_id) is None:
            return False
        del self.cases[case_id]
        return True

    def update_intake(self, actor_id, case_id, expected_state_version, patch) -> CaseView | None:
        current = self.get_case(actor_id, case_id)
        if current is None:
            return None
        if current.state_version != expected_state_version:
            raise StaleCaseError(current.state_version)
        updated_intake = merge_intake(current.intake, patch)
        if updated_intake.model_dump(mode="json") == current.intake.model_dump(mode="json"):
            return current
        now = datetime.now(timezone.utc)
        new_version = current.state_version + 1
        event = AuditEventView(
            id=uuid4(),
            actor_user_id=actor_id,
            event_type="case.intake_updated",
            state_version_before=current.state_version,
            state_version_after=new_version,
            content_revision_before=current.content_revision,
            content_revision_after=current.content_revision + 1,
            metadata={"changed_fields": list(patch.model_dump(exclude_unset=True))},
            occurred_at=now,
        )
        updated = current.model_copy(update={
            "intake": updated_intake,
            "status": "collecting",
            "state_version": new_version,
            "content_revision": current.content_revision + 1,
            "updated_at": now,
            "current_draft": None,
            "approval": None,
            "timeline": [*current.timeline, event],
        })
        self.cases[case_id] = (actor_id, updated)
        return updated


def _client(actor_id: UUID | None = None) -> TestClient:
    user_id = actor_id or UUID("00000000-0000-4000-8000-000000000042")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(user_id))
    app.dependency_overrides[get_case_service] = lambda: CaseService(MemoryCaseRepository())
    return TestClient(app)


def test_case_routes_create_list_read_and_update_with_optimistic_versions() -> None:
    actor = UUID("00000000-0000-4000-8000-000000000042")
    repository = MemoryCaseRepository()
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(actor))
    app.dependency_overrides[get_case_service] = lambda: CaseService(repository)
    client = TestClient(app)

    created = client.post("/v1/cases", json={"scenario_id": "complete"})
    assert created.status_code == 201
    case = created.json()
    case_id = case["id"]
    assert case["synthetic"] is True
    assert case["state_version"] == 1
    assert case["content_revision"] == 1
    assert {result["mode"] for result in case["provider_results"]} == {"mock"}
    assert all(result["id"] == result["source_id"] for result in case["provider_results"])
    assert case["timeline"][0]["event_type"] == "case.created"

    listed = client.get("/v1/cases")
    loaded = client.get(f"/v1/cases/{case_id}")
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert loaded.status_code == 200 and loaded.json()["id"] == case_id
    assert loaded.json()["evidence"] == []
    assert loaded.json()["latest_analysis"] is None

    updated = client.patch(f"/v1/cases/{case_id}/intake", json={
        "expected_state_version": 1,
        "location": "Lille, France (updated synthetic location)",
    })
    assert updated.status_code == 200
    assert updated.json()["state_version"] == 2
    assert updated.json()["content_revision"] == 2
    assert updated.json()["timeline"][-1]["metadata"]["changed_fields"] == ["location"]

    stale = client.patch(f"/v1/cases/{case_id}/intake", json={
        "expected_state_version": 1,
        "narrative": "A stale edit.",
    })
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_case"
    assert stale.json()["error"]["details"]["current_state_version"] == 2


def test_case_routes_reject_unsupported_scenario_and_hide_other_users_cases() -> None:
    owner = UUID("00000000-0000-4000-8000-000000000042")
    repository = MemoryCaseRepository()
    app = create_app()
    active_user = {"id": owner}
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(active_user["id"]))
    app.dependency_overrides[get_case_service] = lambda: CaseService(repository)
    client = TestClient(app)

    invalid = client.post("/v1/cases", json={"scenario_id": "unknown"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "unknown_scenario"

    created = client.post("/v1/cases", json={"scenario_id": "ambiguous"})
    case_id = created.json()["id"]
    active_user["id"] = UUID("00000000-0000-4000-8000-000000000099")
    hidden = client.get(f"/v1/cases/{case_id}")
    assert hidden.status_code == 404


def test_g1_identity_is_persisted_in_list_and_detail_without_inventing_counterparty_plate() -> None:
    owner = UUID("00000000-0000-4000-8000-000000000042")
    other = UUID("00000000-0000-4000-8000-000000000099")
    repository = MemoryCaseRepository()
    app = create_app()
    active_user = {"id": owner}
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(active_user["id"]))
    app.dependency_overrides[get_case_service] = lambda: CaseService(repository)
    client = TestClient(app)

    first = client.post("/v1/cases", json={"scenario_id": "g1"})
    second = client.post("/v1/cases", json={"scenario_id": "g1"})
    complete = client.post("/v1/cases", json={"scenario_id": "complete"})
    ambiguous = client.post("/v1/cases", json={"scenario_id": "ambiguous"})
    assert all(response.status_code == 201 for response in (first, second, complete, ambiguous))
    assert first.json()["id"] != second.json()["id"]
    expected = {
        "insured_reference": "CLM-2026-0842",
        "insured_name": "Camille Martin",
        "insured_vehicle": "Peugeot grise",
        "insured_plate": "FR-482-KL",
        "insured_identity_source": "synthetic_fixture",
    }
    assert {key: first.json()["intake"][key] for key in expected} == expected
    assert "BMW" not in first.json()["intake"]["insured_vehicle"]
    assert first.json()["provider_results"] == []

    listed = client.get("/v1/cases")
    assert listed.status_code == 200
    assert len(listed.json()) == 4
    assert {item["id"] for item in listed.json()} == {response.json()["id"] for response in (first, second, complete, ambiguous)}
    for response in (first, second):
        case_id = response.json()["id"]
        assert {key: client.get(f"/v1/cases/{case_id}").json()["intake"][key] for key in expected} == expected
        assert {key: next(item for item in listed.json() if item["id"] == case_id)["intake"][key] for key in expected} == expected
    for response in (complete, ambiguous):
        identity = response.json()["intake"]
        assert all(identity[key] is None for key in ("insured_name", "insured_vehicle", "insured_plate", "insured_identity_source"))

    active_user["id"] = other
    assert client.get("/v1/cases").json() == []
    assert client.get(f"/v1/cases/{first.json()['id']}").status_code == 404


def test_legacy_intake_without_identity_fields_remains_readable() -> None:
    intake = Intake.model_validate({
        "reported_at": "2025-06-14T16:01:00Z",
        "insured_reference": "CLAIM-SYN-1042",
        "narrative": "Historical synthetic case",
    })
    assert intake.insured_name is None
    assert intake.insured_vehicle is None
    assert intake.insured_plate is None
    assert intake.insured_identity_source is None


def test_case_routes_require_authentication_even_when_database_is_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app())

    response = client.get("/v1/cases")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_case_pages_search_beyond_first_fifty_and_never_include_other_owners():
    repository = MemoryCaseRepository()
    actor = uuid4()
    active = {'id': actor}
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(active['id']))
    app.dependency_overrides[get_case_service] = lambda: CaseService(repository)
    client = TestClient(app)
    target = client.post('/v1/cases', json={'scenario_id': 'g1'}).json()
    client.patch(f"/v1/cases/{target['id']}/intake", json={
        'expected_state_version': 1, 'insured_name': 'Unique Customer'})
    for _ in range(51):
        client.post('/v1/cases', json={'scenario_id': 'g1'})
    assert len(client.get('/v1/cases').json()) == 50  # old array contract
    page = client.get('/v1/cases/page?limit=25').json()
    assert page['total'] == 52 and page['has_more']
    final = client.get('/v1/cases/page?offset=50&limit=25').json()
    assert len(final['items']) == 2 and not final['has_more']
    found = client.get('/v1/cases/page', params={'query': ' Unique Customer '}).json()
    assert found['total'] == 1 and found['items'][0]['id'] == target['id']
    assert client.get('/v1/cases/page?query=%25').json()['total'] == 0
    assert client.get('/v1/cases/page?query=FR482KL').json()['total'] == 52
    active['id'] = uuid4()
    assert client.get('/v1/cases/page?query=Unique').json()['total'] == 0
    assert client.get('/v1/cases/page?offset=-1').status_code == 422


def test_handler_can_resolve_missing_identity_without_rewriting_call_history():
    from claim_api.models import VoiceCaseView
    repository = MemoryCaseRepository()
    actor = uuid4()
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(actor))
    app.dependency_overrides[get_case_service] = lambda: CaseService(repository)
    client = TestClient(app)
    created = client.post('/v1/cases', json={'scenario_id': 'g1'}).json()
    case_id = UUID(created['id'])
    original = repository.get_case(actor, case_id)
    voice = VoiceCaseView(provider='vapi', session_id='original-call', mode='mock', status='incomplete',
                          call_started_at=original.created_at,
                          missing_p0=['insured_name'], facts=[{'field': 'narrative', 'value': 'Original call'}])
    repository.cases[case_id] = (actor, original.model_copy(update={
        'intake': original.intake.model_copy(update={'insured_name': None, 'missing_fields': ['insured_name']}),
        'voice_session': voice,
    }))
    result = client.patch(f'/v1/cases/{case_id}/intake', json={
        'expected_state_version': 1, 'insured_name': '  New Customer  ',
        'incident_at': '2026-09-26T10:00:00+02:00'}).json()
    assert result['intake']['insured_name'] == 'New Customer'
    assert result['intake']['insured_identity_source'] == 'handler_entered'
    assert result['intake']['time_source'] == 'handler_entered'
    assert 'insured_name' not in result['intake']['missing_fields']
    assert result['voice_session'] == voice.model_dump(mode='json')
    assert result['content_revision'] == 2 and result['state_version'] == 2
    assert client.patch(f'/v1/cases/{case_id}/intake', json={
        'expected_state_version': 1, 'insured_name': 'Stale Customer'}).status_code == 409


def test_postgres_pages_scope_and_literal_search(monkeypatch):
    from sqlalchemy import create_engine, text
    from claim_api.postgres_cases import PostgresCaseRepository
    from test_alembic_version import _test_database_url, _apply_alembic
    database_url = _test_database_url()
    _apply_alembic(database_url, monkeypatch)
    engine = create_engine(database_url.replace('postgresql://', 'postgresql+psycopg://', 1))
    actor, outsider = uuid4(), uuid4()
    with engine.begin() as conn:
        conn.execute(text('insert into auth.users(id) values (:owner), (:outsider)'), {'owner': actor, 'outsider': outsider})
    service = CaseService(PostgresCaseRepository(database_url))
    first = service.create_case(str(actor), 'g1')
    service.update_intake(str(actor), first.id, first.state_version, IntakePatch(insured_name='Élodie Search_100% Customer'))
    newest = service.create_case(str(actor), 'g1')
    service.create_case(str(outsider), 'g1')
    page = service.list_cases_page(str(actor), '', 0, 1)
    assert page.total == 2 and page.has_more and page.items[0].id == newest.id
    second = service.list_cases_page(str(actor), '', 1, 1)
    assert second.items[0].id == first.id and not second.has_more
    assert service.list_cases_page(str(actor), '100%', 0, 1).total == 1
    assert service.list_cases_page(str(actor), 'FR482KL', 0, 10).total == 2
    assert service.list_cases_page(str(actor), 'elodie', 0, 1).total == 1
    assert service.list_cases_page(str(actor), '_100%', 0, 1).total == 1
    assert service.list_cases_page(str(outsider), '100%', 0, 1).total == 0
    assert service.list_cases_page(str(actor), 'Camille', 10, 1).total == 1
    engine.dispose()


def test_noop_intake_edit_keeps_revision_and_provenance():
    from claim_api.fixtures import get_scenario
    current = get_scenario('complete').intake
    assert merge_intake(current, IntakePatch(location=current.location)) is current
    assert merge_intake(current, IntakePatch(incident_at=current.incident_at)) is current


def test_current_missing_fields_drop_stale_extraction_labels_and_keep_existing_requirements():
    from claim_api.fixtures import get_scenario
    current = get_scenario('g1').intake.model_copy(update={
        'insured_name': None, 'danger_status': 'unknown', 'injury_status': 'unknown',
        'missing_fields': ['insured_name', 'incident_time', 'third_party_driver', 'danger_status'],
    })
    updated = merge_intake(current, IntakePatch(insured_name='Élodie Martin'))
    assert updated.missing_fields == []
    assert updated.danger_status == updated.injury_status == 'unknown'
    # Null safety answers remain missing, matching the previous merge rules.
    updated = merge_intake(updated, IntakePatch(danger_status=None))
    assert updated.missing_fields == ['danger_status']
    # The insured name is now required even for older cases.
    older = get_scenario('complete').intake
    edited = merge_intake(older, IntakePatch(location='Updated location'))
    assert 'insured_name' in edited.missing_fields


def test_name_and_plate_search_normalizes_diacritics_but_keeps_literal_punctuation():
    assert normalize_case_search('FR-482-KL') == normalize_case_search('fr482kl')
    assert normalize_case_search('Élodie Noël') == normalize_case_search('elodie noel')
    assert normalize_case_search('E\u0301lodie') == normalize_case_search('Élodie')
    assert normalize_case_search('A_100%') == 'a_100%'


def test_delete_case_is_owner_scoped_and_removes_it_from_list_and_detail() -> None:
    owner = uuid4()
    repository = MemoryCaseRepository()
    app = create_app()
    active_user = {"id": owner}
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(active_user["id"]))
    app.dependency_overrides[get_case_service] = lambda: CaseService(repository)
    client = TestClient(app)
    case_id = client.post("/v1/cases", json={"scenario_id": "g1"}).json()["id"]
    keep_id = client.post("/v1/cases", json={"scenario_id": "g2"}).json()["id"]

    active_user["id"] = uuid4()
    assert client.delete(f"/v1/cases/{case_id}").status_code == 404
    active_user["id"] = owner
    assert client.get(f"/v1/cases/{case_id}").status_code == 200
    deleted = client.delete(f"/v1/cases/{case_id}")
    assert deleted.status_code == 204 and deleted.content == b""
    assert client.get(f"/v1/cases/{case_id}").status_code == 404
    assert client.delete(f"/v1/cases/{case_id}").status_code == 404
    assert [item["id"] for item in client.get("/v1/cases").json()] == [keep_id]


def test_delete_case_requires_authentication(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert TestClient(create_app()).delete(f"/v1/cases/{uuid4()}").status_code == 401
