from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from claim_api.accident_journey import photo_video_match
from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.main import create_app
from claim_api.routes.accident_journey import journey as journey_dependency
from test_accident_journey import assessment_for


def test_photo_video_match_requires_plate_cited_in_both_sources():
    photo_id = uuid4()
    case = SimpleNamespace(evidence=[SimpleNamespace(id=photo_id, mime_type='image/png')])
    assessment = assessment_for(case).model_dump(mode='json')
    review = {'assessment': assessment}
    assert photo_video_match(case, None, photo_id)['status'] == 'pending'
    assert photo_video_match(case, review, photo_id)['status'] == 'no_match'

    plate = assessment['media']['plates'][0]
    plate['citations'].append({'evidence_id': str(photo_id), 'timestamp_seconds': None})
    result = photo_video_match(case, review, photo_id)
    assert result == {'status': 'matched', 'photo_id': str(photo_id),
                      'plate': 'FR482KL', 'video_id': assessment['selected_video_id']}

    assessment['media']['plates'][1]['citations'].append({'evidence_id': str(photo_id), 'timestamp_seconds': None})
    assert photo_video_match(case, review, photo_id)['status'] == 'ambiguous'
    with pytest.raises(ValueError):
        photo_video_match(case, review, uuid4())


def test_photo_video_route_restricts_lookup_to_case_owner():
    owner, case_id, photo_id = uuid4(), uuid4(), uuid4()
    case = SimpleNamespace(evidence=[SimpleNamespace(id=photo_id, mime_type='image/png')])
    assessment = assessment_for(case).model_dump(mode='json')
    assessment['media']['plates'][0]['citations'].append({
        'evidence_id': str(photo_id), 'timestamp_seconds': None,
    })

    class Repository:
        def get_case(self, actor, requested_id):
            return case if actor == owner and requested_id == case_id else None

    class Service:
        repository = Repository()
        def view(self, actor, requested_id):
            assert actor == owner and requested_id == case_id
            return {'review': {'assessment': assessment}}

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(owner))
    app.dependency_overrides[journey_dependency] = Service
    client = TestClient(app)
    path = f'/v1/cases/{case_id}/journey/photos/{photo_id}/video-match'
    assert client.get(path).json()['status'] == 'matched'
    assert client.get(f'/v1/cases/{uuid4()}/journey/photos/{photo_id}/video-match').status_code == 404
    assert client.get(f'/v1/cases/{case_id}/journey/photos/{uuid4()}/video-match').status_code == 404
