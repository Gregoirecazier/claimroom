"""Persistent reference data, source integrity, access control and journey integration."""
from contextlib import contextmanager
from datetime import date
import hashlib
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from claim_api.analysis import AnalysisError
from claim_api.astra_analysis import catalogue
from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.demo_media import MEDIA_ROOT
from claim_api.main import create_app
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.reference_data import ReferenceData
from claim_api.routes.reference_data import reference_data
from test_accident_journey import journey_case
from test_insured_portal import portal_db
from test_alembic_version import _apply_alembic, _test_database_url

ORIGINAL_NAMES = {
    'g1': '9275878b-acf4-4a60-9f27-df34d226a68d.mp4',
    'g2': 'cea24e58-62ab-4fb7-8cd0-e1f32f50bf92.mp4',
    'g3': '98ea44f9-0ac7-46b8-8a08-79cf32cbde1a.mp4',
}


@pytest.fixture
def references(monkeypatch):
    url = _test_database_url()
    _apply_alembic(url,monkeypatch)
    return ReferenceData(PostgresCaseRepository(url))


@contextmanager
def changed_row(repository, table, key, identifier, field, value):
    # Identifiers are fixed test literals; values remain bound parameters.
    with repository._connection() as conn:
        original = conn.execute(f'select {field} from public.{table} where {key}=%s',(identifier,)).fetchone()[field]
        conn.execute(f'update public.{table} set {field}=%s where {key}=%s',(value,identifier))
    try:
        yield
    finally:
        if isinstance(original,(list,dict)): original = Jsonb(original)
        with repository._connection() as conn:
            conn.execute(f'update public.{table} set {field}=%s where {key}=%s',(original,identifier))


def test_migration_imports_complete_contacts_and_exact_video_sources(references,monkeypatch):
    repo = references.repository
    with repo._connection() as conn:
        assert conn.execute('select country,count(*) as n from public.mock_vehicle_registry group by country order by country').fetchall() == [
            {'country':'FR','n':1000},{'country':'UK','n':1000}]
        assert conn.execute("""select count(*) as n from public.mock_vehicle_registry where
            driver_name<>'' and driver_email like '%@%.test' and insurer_id<>'' and insurer_name<>''
            and insurer_contact_name<>'' and insurer_email like '%@%.test' and synthetic""").fetchone()['n'] == 2000
        assert conn.execute("select count(*) as n from public.mock_vehicle_registry where policy_reference is null").fetchone()['n'] == 100
    entries = references.video_catalogue()
    assert len(entries) == 3
    assert len({entry['sha256'] for entry in entries}) == 3
    assert sum(len(entry['frames']) for entry in entries) == 60
    for entry in entries:
        video = MEDIA_ROOT / entry['path']
        assert entry['original_filename'] == ORIGINAL_NAMES[video.parent.name]
        assert entry['sha256'] == hashlib.sha256(video.read_bytes()).hexdigest()
        assert entry['byte_size'] == video.stat().st_size
        assert entry['duration_seconds'] == 5.04
        assert entry['source_kind'] == 'simulated_city_camera'
        assert all((MEDIA_ROOT / frame['path']).is_file() for frame in entry['frames'])
    # Re-running the migration chain is a no-op, with no duplicate rows or videos.
    _apply_alembic(repo.database_url,monkeypatch)
    assert references.vehicles()['total'] == 2000
    assert references.videos()['total'] == 3


def test_policy_lookup_uses_database_contacts_without_guessing_plates_or_coverage(references):
    with changed_row(references.repository,'mock_vehicle_registry','plate_normalized','AB12CDE',
                     'insurer_email','gestionnaire-mis-a-jour@example.test'):
        result = references.insurance_lookup('ab12 cde','GB',date(2026,9,25))
        assert result.status == 'matched'
        assert result.data['insurer_email'] == 'gestionnaire-mis-a-jour@example.test'
        assert result.data['driver_email'] == 'conducteur-uk-0001@example.test'
    assert references.insurance_lookup('AB12 CD?','UK',date(2026,9,25)).status == 'ambiguous'
    assert references.insurance_lookup('ZZ99 ZZZ','UK',date(2026,9,25)).reason == 'plate_not_found'
    assert references.insurance_lookup('AB15 CDE','UK',date(2026,9,25)).reason == 'policy_not_found'
    assert references.insurance_lookup('AB13 CDE','UK',date(2026,9,25)).reason == 'coverage_expired'


def test_disabled_video_is_excluded_from_new_matches_but_remains_in_existing_reviews(references):
    identifier = UUID(catalogue()[0]['id'])
    with changed_row(references.repository,'accident_video_catalogue','id',identifier,'active',False):
        assert len(references.video_catalogue()) == 2
        assert len(references.video_catalogue(identifier)) == 1
    assert references.video_catalogue(uuid4()) == []


@pytest.mark.parametrize('field,value',[
    ('sha256','0'*64), ('media_path','../../outside.mp4'), ('frames',Jsonb([])),
])
def test_database_video_metadata_must_agree_with_deployed_sources(references,field,value):
    identifier = UUID(catalogue()[0]['id'])
    with changed_row(references.repository,'accident_video_catalogue','id',identifier,field,value):
        with pytest.raises(AnalysisError) as error:
            references.video_catalogue(identifier)
        assert error.value.code == 'catalogue_out_of_sync'


def test_reference_endpoints_require_auth_and_return_exact_contacts_and_video_bytes(references,monkeypatch):
    monkeypatch.setenv('LOCAL_DEV_AUTH','false')
    app = create_app()
    app.dependency_overrides[reference_data] = lambda: references
    client = TestClient(app)
    identifier = catalogue()[0]['id']
    for path in ('vehicles','videos',f'videos/{identifier}/content'):
        assert client.get('/v1/reference-data/'+path).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(uuid4()))
    response = client.get('/v1/reference-data/vehicles?country=GB&plate=ab12%20cde')
    assert response.status_code == 200,response.text
    assert response.json()['total'] == 1
    assert response.json()['items'][0]['insurer_name'] == 'Northbridge Demo Motor (fictional)'
    assert client.get('/v1/reference-data/vehicles?plate=AB12').json()['total'] == 0
    page = client.get('/v1/reference-data/vehicles?country=FR&limit=2&offset=2').json()
    assert page['total'] == 1000 and len(page['items']) == 2
    assert client.get('/v1/reference-data/vehicles?limit=201').status_code == 422
    listing = client.get('/v1/reference-data/videos').json()
    assert listing['total'] == 3
    for entry in listing['items']:
        response = client.get(f"/v1/reference-data/videos/{entry['id']}/content")
        assert response.status_code == 200
        assert response.headers['content-type'] == 'video/mp4'
        assert response.headers['cache-control'] == 'private, no-store'
        assert hashlib.sha256(response.content).hexdigest() == entry['sha256']
    assert client.get(f'/v1/reference-data/videos/{uuid4()}/content').status_code == 404


def test_automatic_journey_saves_the_contact_from_the_database(journey_case):
    repo,journey,flow,actor,case_id,*_ = journey_case
    with changed_row(repo,'mock_vehicle_registry','plate_normalized','AB12CDE',
                     'insurer_email','equipe-recours@example.test'):
        assert flow.tick(case_id)
        result = journey.view(actor,case_id)
    assert result['catalogue_count'] == 3
    assert result['review']['status'] == 'awaiting_review'
    responsible = next(match for match in result['review']['insurance_matches'] if match['role']=='third_party')
    assert responsible['data']['insurer_email'] == 'equipe-recours@example.test'
    assert responsible['data']['driver_email'].endswith('@example.test')
