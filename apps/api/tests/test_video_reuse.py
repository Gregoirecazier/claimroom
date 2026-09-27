from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from claim_api.models import EvidenceView
from claim_api.video_reuse import (
    VideoAnalysisRequest, VideoChecksumMismatch, VideoObservation,
    VideoPipelineSpec, VideoReuseService,
)


ACTOR = uuid4()
VIDEO = b"synthetic original MP4 bytes"
DIGEST = hashlib.sha256(VIDEO).hexdigest()


def pipeline(**changes) -> VideoPipelineSpec:
    values = dict(analyzer_name="test-vision", model_id="vision-exact-2026-09",
                  prompt_version="p1", output_schema_version=1, frame_sampling="1fps",
                  resize_policy="640px", ocr_version="ocr-1", preprocessing_version="p1",
                  deterministic_parameters={"temperature": 0})
    values.update(changes)
    return VideoPipelineSpec(**values)


class MemoryStorage:
    def __init__(self, data: bytes = VIDEO) -> None:
        self.data = data
        self.reads = 0

    def read_object_chunks(self, _path):
        self.reads += 1
        yield self.data[:10]
        yield self.data[10:]


class MemoryRepository:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.evidence: dict[tuple[UUID, UUID, UUID], EvidenceView] = {}
        self.artifacts = {}
        self.links = set()

    def add(self, actor=ACTOR, case_id=None, *, verified=True, declared=DIGEST, verified_digest=DIGEST):
        case_id = case_id or uuid4()
        evidence_id = uuid4()
        self.evidence[(actor, case_id, evidence_id)] = EvidenceView(
            id=evidence_id, case_id=case_id, kind="video", storage_path=f"{case_id}/{evidence_id}.mp4",
            source_kind="handler_upload", mode="live", mime_type="video/mp4", byte_size=len(VIDEO),
            client_sha256=declared, checksum_status="verified" if verified else "client_declared",
            sha256_verified=verified_digest if verified else None, received_at=datetime.now(timezone.utc),
        )
        return actor, case_id, evidence_id

    def owned_evidence(self, actor, case, evidence):
        return self.evidence.get((actor, case, evidence))

    def has_incompatible_artifact(self, scope, digest, fingerprint):
        return any(key[0] == scope and key[1] == digest and key[2] != fingerprint
                   and item["status"] == "succeeded" for key, item in self.artifacts.items())

    def verify(self, actor, case, evidence, digest):
        key = actor, case, evidence
        item = self.evidence[key]
        if digest != item.client_sha256:
            self.evidence[key] = item.model_copy(update={"checksum_status": "mismatch"})
            raise VideoChecksumMismatch
        self.evidence[key] = item.model_copy(update={"checksum_status": "verified", "sha256_verified": digest})
        return self.evidence[key]

    def claim(self, scope, digest, spec, policy):
        key = scope, digest, spec.fingerprint
        with self.lock:
            item = self.artifacts.get(key)
            if item is None and policy == "reuse_only":
                return "not_preprocessed", None
            if item is None:
                item = dict(id=uuid4(), attempt_id=uuid4(), status="running", observations_json=None, usage_json={})
                self.artifacts[key] = item
                return "claimed", dict(item)
            if item["status"] == "succeeded":
                return "reused", dict(item)
            if item["status"] == "running":
                return "processing", dict(item)
            if policy == "reuse_only":
                return "not_preprocessed", dict(item)
            item.update(attempt_id=uuid4(), status="running")
            return "claimed", dict(item)

    def complete(self, artifact_id, attempt_id, observations, usage):
        with self.lock:
            item = next(x for x in self.artifacts.values() if x["id"] == artifact_id)
            if item["attempt_id"] != attempt_id:
                return False
            item.update(status="succeeded", observations_json=[x.model_dump() for x in observations], usage_json=usage)
            return True

    def fail(self, artifact_id, attempt_id, _code):
        with self.lock:
            item = next(x for x in self.artifacts.values() if x["id"] == artifact_id)
            if item["attempt_id"] == attempt_id:
                item["status"] = "failed"

    def link(self, actor, case, evidence, artifact_id):
        if (actor, case, evidence) not in self.evidence:
            return False
        self.links.add((case, evidence, artifact_id))
        return True


class CountingAnalyzer:
    def __init__(self, entered=None, release=None, fail=False):
        self.calls = 0
        self.entered = entered
        self.release = release
        self.fail = fail

    def analyze(self, path: Path, _spec):
        self.calls += 1
        assert path.read_bytes() == VIDEO
        if self.entered:
            self.entered.set()
        if self.release:
            assert self.release.wait(timeout=5)
        if self.fail:
            raise RuntimeError("provider failure")
        return [VideoObservation(start_ms=100, end_ms=900, category="movement",
                                 description="A vehicle moves left to right.")], {"cost_minor": 7}


def run(service, identity, policy="reuse_only"):
    actor, case, evidence = identity
    return service.run(str(actor), case, evidence, VideoAnalysisRequest(processing_policy=policy))


def test_reuse_only_miss_never_calls_analyzer_and_same_bytes_reuse_with_case_local_refs():
    repo, storage, analyzer = MemoryRepository(), MemoryStorage(), CountingAnalyzer()
    first, second = repo.add(), repo.add()
    service = VideoReuseService(repo, storage, pipeline(), analyzer)
    assert run(service, first).status == "not_preprocessed"
    assert analyzer.calls == 0
    prepared = run(service, first, "allow_new")
    replay = run(VideoReuseService(repo, storage, pipeline()), second)
    assert prepared.status == "completed"
    assert replay.status == "reused" and replay.analysis_reused is True
    assert analyzer.calls == 1
    assert prepared.artifact_id == replay.artifact_id
    assert prepared.observations[0].source_ref.id == str(first[2])
    assert replay.observations[0].source_ref.id == str(second[2])
    assert replay.observations[0].source_ref.locator == "video:100-900"
    assert replay.estimated_cost_saved_minor == 7
    assert len(repo.links) == 2


def test_scope_pipeline_and_verified_bytes_bound_the_key():
    repo, storage, analyzer = MemoryRepository(), MemoryStorage(), CountingAnalyzer()
    first = repo.add()
    other_user = repo.add(actor=uuid4())
    changed_byte = repo.add(verified_digest=hashlib.sha256(VIDEO + b"!").hexdigest())
    VideoReuseService(repo, storage, pipeline(), analyzer).run(str(first[0]), first[1], first[2],
        VideoAnalysisRequest(processing_policy="allow_new"))
    assert run(VideoReuseService(repo, storage, pipeline(), analyzer), other_user).status == "not_preprocessed"
    assert run(VideoReuseService(repo, storage, pipeline(), analyzer), changed_byte).status == "not_preprocessed"
    for change in (dict(model_id="new-model"), dict(prompt_version="p2"),
                   dict(frame_sampling="2fps"), dict(ocr_version="ocr-2")):
        result = run(VideoReuseService(repo, storage, pipeline(**change), analyzer), first)
        assert result.status == "not_preprocessed" and result.pipeline_changed
    assert analyzer.calls == 1


def test_misleading_client_hash_cannot_hit_or_call_provider():
    repo, storage, analyzer = MemoryRepository(), MemoryStorage(), CountingAnalyzer()
    correct = repo.add()
    VideoReuseService(repo, storage, pipeline(), analyzer).run(str(correct[0]), correct[1], correct[2],
        VideoAnalysisRequest(processing_policy="allow_new"))
    liar = repo.add(verified=False, declared="0" * 64)
    with pytest.raises(VideoChecksumMismatch):
        run(VideoReuseService(repo, storage, pipeline(), analyzer), liar)
    assert repo.evidence[liar].checksum_status == "mismatch"
    assert analyzer.calls == 1


def test_concurrent_requests_claim_one_paid_analysis():
    entered, release = threading.Event(), threading.Event()
    repo, storage, analyzer = MemoryRepository(), MemoryStorage(), CountingAnalyzer(entered, release)
    first, second = repo.add(), repo.add()
    service = VideoReuseService(repo, storage, pipeline(), analyzer)
    result = []
    worker = threading.Thread(target=lambda: result.append(run(service, first, "allow_new")))
    worker.start()
    assert entered.wait(timeout=5)
    in_flight = run(service, second, "allow_new")
    assert in_flight.status == "processing" and analyzer.calls == 1
    release.set()
    worker.join(timeout=5)
    assert len(result) == 1 and result[0].status == "completed"
    assert run(service, second).status == "reused"


def test_failed_result_is_never_reused_and_retry_is_explicit():
    repo, storage, failing = MemoryRepository(), MemoryStorage(), CountingAnalyzer(fail=True)
    identity = repo.add()
    with pytest.raises(RuntimeError):
        run(VideoReuseService(repo, storage, pipeline(), failing), identity, "allow_new")
    assert run(VideoReuseService(repo, storage, pipeline(), failing), identity).status == "not_preprocessed"
    healthy = CountingAnalyzer()
    assert run(VideoReuseService(repo, storage, pipeline(), healthy), identity, "allow_new").status == "completed"
    assert healthy.calls == 1
