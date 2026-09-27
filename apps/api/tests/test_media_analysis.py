from types import SimpleNamespace
from uuid import uuid4

import pytest

from claim_api.analysis import AnalysisError
from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.evidence_service import EvidenceService
from claim_api.media_analysis import MediaAnalysisService
from claim_api.models import AnalysisRunRequest, FinalizeEvidenceRequest
from claim_api.storage import StoredObjectInfo
from test_analysis import ACTOR, MemoryAnalysisRepository
from test_evidence_service import FakeStorage, MemoryEvidenceRepository, _upload_request
from test_gemini_analysis import case_with_media, analyzer_for


def test_media_run_calls_gemini_and_reuses_only_current_revision():
    case = case_with_media()
    analyzer, server, _ = analyzer_for(case)
    repo = MemoryAnalysisRepository(case)
    service = MediaAnalysisService(repo, analyzer=analyzer)
    first = service.run(str(ACTOR), case.id, AnalysisRunRequest(expected_state_version=case.state_version))
    assert first.analysis_run.status == 'ready'
    assert server.generation_calls == 1
    reused = service.run(str(ACTOR), case.id, AnalysisRunRequest(expected_state_version=repo.case.state_version))
    assert reused.analysis_run.id == first.analysis_run.id
    assert server.generation_calls == 1
    repo.case.content_revision += 1
    service.run(str(ACTOR), case.id, AnalysisRunRequest(expected_state_version=repo.case.state_version))
    assert server.generation_calls == 2


def test_media_reuse_checks_authorization_and_state_version():
    case = case_with_media()
    analyzer, _, _ = analyzer_for(case)
    service = MediaAnalysisService(MemoryAnalysisRepository(case), analyzer=analyzer)
    with pytest.raises(CaseNotFoundError):
        service.run(str(uuid4()), case.id, AnalysisRunRequest(expected_state_version=case.state_version))
    with pytest.raises(StaleCaseError):
        service.run(str(ACTOR), case.id, AnalysisRunRequest(expected_state_version=case.state_version + 1))


def test_failed_auto_analysis_keeps_evidence_and_requires_explicit_retry():
    case = case_with_media()
    calls = []
    def fail(snapshot):
        calls.append(snapshot)
        raise AnalysisError('gemini_rate_limited', 'Gemini temporarily unavailable')
    analyzer = SimpleNamespace(method_version='gemini-joint-media-v1:test', mode='live', analyze=fail)
    repo = MemoryAnalysisRepository(case)
    service = MediaAnalysisService(repo, analyzer=analyzer)
    updated = service.received(str(ACTOR), case)
    assert updated.latest_analysis.status == 'failed'
    assert len(updated.evidence) == 2
    service.received(str(ACTOR), updated)
    assert len(calls) == 1
    service.run(str(ACTOR), case.id, AnalysisRunRequest(expected_state_version=updated.state_version))
    assert len(calls) == 2


@pytest.mark.parametrize('mime,kind', [('video/mp4','cctv_video'), ('video/mp4','scene_video'),
    ('video/quicktime','insured_video'), ('video/webm','cctv_video'), ('image/png','damage_photo'),
    ('image/jpeg','scene_photo'),('application/pdf','document')])
def test_receipt_analyzes_each_committed_photo_or_video_once(mime, kind):
    repo = MemoryEvidenceRepository('g1')
    storage = FakeStorage()
    received = []
    def analyze(actor, updated):
        assert updated.evidence and updated.evidence[-1].mime_type == mime
        assert repo.intents[updated.evidence[-1].storage_path].finalized_evidence_id
        received.append(updated.id)
        return updated
    service = EvidenceService(repo, storage, on_media_received=analyze)
    intent = service.create_upload_intent(str(ACTOR), repo.case.id,
        _upload_request(repo.case, mime_type=mime, kind=kind, byte_size=512))
    storage.objects[intent.storage_path] = StoredObjectInfo(byte_size=512, mime_type=mime)
    request = FinalizeEvidenceRequest(storage_path=intent.storage_path, client_sha256='a'*64,
        kind=kind, expected_state_version=repo.case.state_version)
    service.finalize_upload(str(ACTOR), repo.case.id, request)
    service.finalize_upload(str(ACTOR), repo.case.id, request)
    assert len(received) == (1 if mime.startswith(('video/','image/')) else 0)


def test_explicit_media_dependency_uses_gemini_even_when_text_provider_is_pipelex(monkeypatch):
    from claim_api.routes import analysis as routes
    repo = MemoryAnalysisRepository(case_with_media())
    monkeypatch.setenv('ANALYSIS_PROVIDER', 'pipelex')
    monkeypatch.setattr(routes, 'get_analysis_service', lambda: SimpleNamespace(repository=repo))
    service = routes.get_media_analysis_service()
    assert service.analyzer.provider == 'google'
    assert service.repository is repo


def test_media_route_requires_login_and_returns_saved_gemini_results():
    from fastapi.testclient import TestClient
    from claim_api.auth import AuthenticatedUser, get_current_user
    from claim_api.main import create_app
    from claim_api.routes.analysis import get_media_analysis_service
    case = case_with_media()
    analyzer, server, _ = analyzer_for(case)
    repo = MemoryAnalysisRepository(case)
    app = create_app()
    app.dependency_overrides[get_media_analysis_service] = lambda: MediaAnalysisService(repo, analyzer=analyzer)
    client = TestClient(app)
    path = f'/v1/cases/{case.id}/media-analysis-runs'
    body = {'expected_state_version': case.state_version}
    assert client.post(path, json=body).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(ACTOR), email='demo@example.test')
    response = client.post(path, json=body)
    assert response.status_code == 200
    assert response.json()['analysis_run']['output']['media_analysis']['observations']
    repeated = client.post(path, json={'expected_state_version': response.json()['case']['state_version']})
    assert repeated.status_code == 200
    assert server.generation_calls == 1


def test_camera_receipt_triggers_analysis_only_after_commit_and_only_once():
    from claim_api.cctv_fixture import CCTV_FIXTURE_EVENT_ID
    from claim_api.models import ReceiveCCTVRequest
    repo = MemoryEvidenceRepository('complete')
    received = []
    def analyze(actor, case):
        assert case.evidence[-1].kind == 'cctv_frame'
        assert any(event.event_type == 'case.cctv_received' for event in case.timeline)
        received.append(case.id)
        return case
    service = EvidenceService(repo, FakeStorage(), on_media_received=analyze)
    request = ReceiveCCTVRequest(fixture_event_id=CCTV_FIXTURE_EVENT_ID, expected_state_version=repo.case.state_version)
    service.receive_cctv(str(ACTOR), repo.case.id, request)
    service.receive_cctv(str(ACTOR), repo.case.id, request)
    assert received == [repo.case.id]
