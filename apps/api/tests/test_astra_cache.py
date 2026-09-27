"""Complete-request caching must avoid paid calls without hiding changed inputs."""
import hashlib
import json
import threading
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest

import claim_api.astra_analysis as astra
from claim_api.analysis import AnalysisError
from claim_api.video_reuse import PostgresVideoRepository
from test_accident_journey import assessment_for, journey_case, portal_db
from test_analysis import make_case
from test_video_reuse import MemoryRepository
from test_vision import evidence


@pytest.fixture
def joint_request(tmp_path, monkeypatch):
    monkeypatch.setattr(astra, 'MEDIA_ROOT', tmp_path)
    (tmp_path / 'frame.jpg').write_bytes(b'catalogue image')
    case = make_case()
    photo = evidence().model_copy(update={'case_id': case.id, 'storage_path': f'{case.id}/photo.png'})
    case = case.model_copy(update={'evidence': [photo]})
    entries = [{'id': str(uuid4()), 'frames': [{'seconds': 0.125, 'path': 'frame.jpg'}]}]
    from test_vision import PHOTO
    storage = SimpleNamespace(read_object=lambda *args: PHOTO)
    calls = []
    def provider(request, timeout):
        calls.append(json.loads(request.data))
        result = assessment_for(case, entries)
        return BytesIO(json.dumps({'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': result.model_dump_json()}]},
        ]}).encode())
    return case, entries, storage, provider, calls


def test_joint_cache_replays_without_key_and_survives_unrelated_revision(joint_request):
    case, entries, storage, provider, calls = joint_request
    cache = MemoryRepository()
    analyzer = astra.AstraAccidentAnalyzer(storage, 'test', provider, cache)
    expected = analyzer.analyze(case, entries)
    # No provider key is needed to consume an existing valid response.
    restarted = astra.AstraAccidentAnalyzer(storage, '', provider, cache)
    updated = case.model_copy(update={'content_revision': case.content_revision + 1})
    assert restarted.analyze(updated, entries) == expected
    assert restarted.analysis_reused and len(calls) == 1


@pytest.mark.parametrize('change', ['prompt', 'version', 'model', 'narrative', 'photo', 'frame', 'owner'])
def test_joint_cache_invalidates_every_relevant_input(joint_request, monkeypatch, tmp_path, change):
    case, entries, storage, provider, calls = joint_request
    analyzer = astra.AstraAccidentAnalyzer(storage, 'test', provider, MemoryRepository())
    analyzer.analyze(case, entries)
    if change == 'prompt': monkeypatch.setattr(astra, 'PROMPT', astra.PROMPT + '\nUpdated instruction.')
    elif change == 'version': analyzer.method_version += '.1'
    elif change == 'model': analyzer.model = 'different-model'
    elif change == 'narrative': case = case.model_copy(update={'intake': case.intake.model_copy(update={'narrative': 'Updated narrative'})})
    elif change == 'photo':
        data = b'new image bytes'
        case = case.model_copy(update={'evidence': [case.evidence[0].model_copy(update={
            'byte_size': len(data), 'client_sha256': hashlib.sha256(data).hexdigest()})]})
        storage.read_object = lambda *args: data
    elif change == 'frame': (tmp_path / 'frame.jpg').write_bytes(b'updated video frame')
    elif change == 'owner': case = case.model_copy(update={'created_by_user_id': uuid4()})
    analyzer.analyze(case, entries)
    assert len(calls) == 2 and not analyzer.analysis_reused


def test_joint_cache_serializes_concurrent_calls(joint_request):
    case, entries, storage, provider, calls = joint_request
    entered, release = threading.Event(), threading.Event()
    def slow_provider(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return provider(*args, **kwargs)
    cache = MemoryRepository()
    first = astra.AstraAccidentAnalyzer(storage, 'test', slow_provider, cache)
    second = astra.AstraAccidentAnalyzer(storage, 'test', provider, cache)
    results = []
    worker = threading.Thread(target=lambda: results.append(first.analyze(case, entries)))
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(AnalysisError) as error: second.analyze(case, entries)
        assert error.value.code == 'astra_analysis_in_progress'
    finally:
        release.set(); worker.join(5)
    assert second.analyze(case, entries) == results[0]
    assert second.analysis_reused and len(calls) == 1


def test_joint_cache_does_not_reuse_failed_responses(joint_request):
    case, entries, storage, provider, calls = joint_request
    cache = MemoryRepository()
    failed = astra.AstraAccidentAnalyzer(storage, 'test', lambda *a, **k: BytesIO(b'{"status":"incomplete"}'), cache)
    with pytest.raises(AnalysisError): failed.analyze(case, entries)
    analyzer = astra.AstraAccidentAnalyzer(storage, 'test', provider, cache)
    analyzer.analyze(case, entries)
    analyzer.analyze(case, entries)
    assert analyzer.analysis_reused and len(calls) == 1


def test_joint_cache_persists_in_postgres(journey_case):
    repo, _, _, actor, case_id, *_ = journey_case
    case = repo.get_case(actor, case_id)
    response = assessment_for(case)
    calls = []
    def provider(request, timeout):
        calls.append(1)
        return BytesIO(json.dumps({'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': response.model_dump_json()}]},
        ]}).encode())
    data = {hashlib.sha256(p.read_bytes()).hexdigest(): p.read_bytes() for p in (astra.MEDIA_ROOT / 'g1').glob('*.png')}
    paths = {e.storage_path: data[e.client_sha256] for e in case.evidence}
    storage = SimpleNamespace(read_object=lambda path, *_: paths[path])
    first = astra.AstraAccidentAnalyzer(storage, 'test', provider, PostgresVideoRepository(repo))
    assert first.analyze(case) == response
    second = astra.AstraAccidentAnalyzer(storage, '', provider, PostgresVideoRepository(repo))
    assert second.analyze(case) == response
    assert second.analysis_reused and len(calls) == 1
