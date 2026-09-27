"""Verified, scope-bound reuse of intrinsic video observations.

The analyzer receives only video bytes and a versioned pipeline definition. It
cannot receive a case, claimant narrative, insurer lookup, or signed URL.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Sequence
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from psycopg.types.json import Jsonb

from claim_api.evidence_service import EvidenceNotFoundError, actor_uuid
from claim_api.models import AnalysisSourceRef, ContractModel, EvidenceView
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.storage import StorageAdapter, StorageAdapterError


MAX_VIDEO_BYTES = 10_000_000


class VideoPipelineSpec(ContractModel):
    analyzer_name: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    output_schema_version: int = Field(ge=1)
    frame_sampling: str = Field(min_length=1)
    resize_policy: str = Field(min_length=1)
    ocr_version: str = Field(min_length=1)
    preprocessing_version: str = Field(min_length=1)
    deterministic_parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def configured_pipeline() -> VideoPipelineSpec:
    parameters = json.loads(os.getenv("VIDEO_DETERMINISTIC_PARAMETERS_JSON", "{}"))
    if not isinstance(parameters, dict):
        raise ValueError("VIDEO_DETERMINISTIC_PARAMETERS_JSON must be an object.")
    return VideoPipelineSpec(
        analyzer_name=os.getenv("VIDEO_ANALYZER_NAME", "unconfigured"),
        model_id=os.getenv("VIDEO_MODEL_ID", "unconfigured"),
        prompt_version=os.getenv("VIDEO_PROMPT_VERSION", "v1"),
        output_schema_version=1,
        frame_sampling=os.getenv("VIDEO_FRAME_SAMPLING", "1fps"),
        resize_policy=os.getenv("VIDEO_RESIZE_POLICY", "original"),
        ocr_version=os.getenv("VIDEO_OCR_VERSION", "none"),
        preprocessing_version=os.getenv("VIDEO_PREPROCESSING_VERSION", "v1"),
        deterministic_parameters=parameters,
    )


def configured_vision_pipeline() -> VideoPipelineSpec:
    base = configured_pipeline()
    if os.getenv("VISION_FIXTURE_MOCK", "").strip() == "1":
        return base.model_copy(update={
            "analyzer_name": "fixture-vision",
            "model_id": "exact-media-script-v1",
            "prompt_version": "fixture-observations-v1",
            "output_schema_version": 2,
        })
    return base.model_copy(update={"output_schema_version": 2})


class VideoObservation(ContractModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    category: Literal["vehicle", "visible_text", "damage", "movement", "other"]
    description: str = Field(min_length=1, max_length=1000)
    status: Literal["observed", "uncertain", "not_visible"] = "observed"
    uncertainty: str | None = Field(default=None, max_length=500)
    vehicle_track_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    plate_candidate: str | None = Field(default=None, max_length=32)
    uncertain_positions: list[int] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    calibration: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_time_range(self) -> "VideoObservation":
        if self.end_ms < self.start_ms:
            raise ValueError("Video observation end must follow start.")
        if self.uncertainty and self.status == "observed":
            self.status = "uncertain"
        return self

    @property
    def locator(self) -> str:
        return f"video:{self.start_ms}-{self.end_ms}"


class VideoObservationSource(ContractModel):
    observation: VideoObservation
    source_ref: AnalysisSourceRef


class VideoAnalysisRequest(ContractModel):
    processing_policy: Literal["reuse_only", "allow_new"] = "reuse_only"


class VideoAnalysisResponse(ContractModel):
    status: Literal["reused", "completed", "processing", "not_preprocessed"]
    artifact_id: UUID | None = None
    pipeline_fingerprint: str
    analysis_reused: bool
    observations: list[VideoObservationSource] = Field(default_factory=list)
    estimated_cost_saved_minor: int | None = None
    pipeline_changed: bool = False


class VideoAnalyzer(Protocol):
    def analyze(self, video_path: Path, pipeline: VideoPipelineSpec) -> tuple[list[VideoObservation], dict[str, Any]]: ...


class VideoChecksumMismatch(ValueError):
    pass


class VideoSourceChanged(ValueError):
    pass


class VideoAnalyzerUnavailable(RuntimeError):
    pass


def _safe_usage(usage: dict[str, Any]) -> dict[str, int]:
    """Persist numeric usage only; never save a provider response or URL."""
    allowed = ("input_tokens", "output_tokens", "cost_minor", "duration_ms")
    return {key: value for key in allowed if isinstance((value := usage.get(key)), int)
            and not isinstance(value, bool) and value >= 0}


def _source_observations(evidence_id: UUID, observations: list[VideoObservation]) -> list[VideoObservationSource]:
    return [VideoObservationSource(
        observation=item,
        source_ref=AnalysisSourceRef(kind="evidence", id=str(evidence_id), locator=item.locator),
    ) for item in observations]


class PostgresVideoRepository:
    def __init__(self, cases: PostgresCaseRepository) -> None:
        self.cases = cases

    def owned_evidence(self, actor_id: UUID, case_id: UUID, evidence_id: UUID) -> EvidenceView | None:
        return self.cases.get_owned_evidence(actor_id, case_id, evidence_id)

    def has_incompatible_artifact(self, scope: str, digest: str, fingerprint: str) -> bool:
        with self.cases._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """select 1 from public.media_analysis_artifacts
                   where scope=%s and sha256_verified=%s and pipeline_fingerprint<>%s
                     and status='succeeded' limit 1""",
                (scope, digest, fingerprint),
            )
            return cursor.fetchone() is not None

    def verify(self, actor_id: UUID, case_id: UUID, evidence_id: UUID, digest: str | None) -> EvidenceView:
        with self.cases._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """select e.* from public.evidence e join public.cases c on c.id=e.case_id
                   where e.id=%s and e.case_id=%s and c.created_by_user_id=%s for update of e""",
                (evidence_id, case_id, actor_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise EvidenceNotFoundError
            if row["checksum_status"] == "verified":
                if digest is not None and digest != row["sha256_verified"]:
                    raise VideoSourceChanged("The private object changed while verification was in progress.")
                return EvidenceView.model_validate(row)
            if row["checksum_status"] == "mismatch":
                raise VideoChecksumMismatch("The uploaded bytes did not match the declared checksum.")
            # Legacy server-owned evidence can lack a client declaration. The
            # streamed server digest is still authoritative once size matches.
            matched = digest is not None and (
                row["client_sha256"] is None or row["client_sha256"] == digest
            )
            cursor.execute(
                """update public.evidence set checksum_status=%s, sha256_verified=%s
                   where id=%s returning *""",
                ("verified" if matched else "mismatch", digest if matched else None, evidence_id),
            )
            result = cursor.fetchone()
        if not matched:
            raise VideoChecksumMismatch("The uploaded bytes did not match the declared checksum.")
        return EvidenceView.model_validate(result)

    def claim(self, scope: str, digest: str, pipeline: VideoPipelineSpec,
              policy: Literal["reuse_only", "allow_new"]) -> tuple[str, dict[str, Any] | None]:
        fingerprint = pipeline.fingerprint
        with self.cases._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """select * from public.media_analysis_artifacts
                   where scope=%s and sha256_verified=%s and pipeline_fingerprint=%s for update""",
                (scope, digest, fingerprint),
            )
            row = cursor.fetchone()
            if row is None and policy == "reuse_only":
                return "not_preprocessed", None
            if row is None:
                attempt = uuid4()
                cursor.execute(
                    """insert into public.media_analysis_artifacts
                       (scope, sha256_verified, pipeline_fingerprint, pipeline_spec, status,
                        attempt_id, lease_until, output_schema_version)
                       values (%s,%s,%s,%s,'running',%s,now()+interval '15 minutes',%s)
                       on conflict (scope, sha256_verified, pipeline_fingerprint) do nothing returning *""",
                    (scope, digest, fingerprint, Jsonb(pipeline.model_dump(mode="json")), attempt,
                     pipeline.output_schema_version),
                )
                row = cursor.fetchone()
                if row is not None:
                    return "claimed", row
                # Another transaction inserted concurrently. Lock and inspect it.
                cursor.execute(
                    """select * from public.media_analysis_artifacts
                       where scope=%s and sha256_verified=%s and pipeline_fingerprint=%s for update""",
                    (scope, digest, fingerprint),
                )
                row = cursor.fetchone()
            if row["status"] == "succeeded":
                return "reused", row
            if row["status"] == "running" and row["lease_until"] > datetime.now(timezone.utc):
                return "processing", row
            if policy == "reuse_only":
                return "not_preprocessed", row
            # A failed or timed-out attempt can be retried only by allow_new.
            attempt = uuid4()
            if row["status"] == "running":
                cursor.execute(
                    """update public.media_analysis_artifacts
                       set failure_count=failure_count+1,
                           failure_history=failure_history || jsonb_build_array(jsonb_build_object(
                               'attempt_id', attempt_id::text, 'code', 'lease_expired', 'at', now()))
                       where id=%s""",
                    (row["id"],),
                )
            cursor.execute(
                """update public.media_analysis_artifacts
                   set status='running', attempt_id=%s, lease_until=now()+interval '15 minutes',
                       observations_json=null, finished_at=null, last_error_code=null, updated_at=now()
                   where id=%s returning *""",
                (attempt, row["id"]),
            )
            return "claimed", cursor.fetchone()

    def complete(self, artifact_id: UUID, attempt_id: UUID,
                 observations: Sequence[ContractModel], usage: dict[str, Any]) -> bool:
        with self.cases._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """update public.media_analysis_artifacts
                   set status='succeeded', observations_json=%s, usage_json=%s,
                       lease_until=null, finished_at=now(), updated_at=now()
                   where id=%s and attempt_id=%s and status='running' returning id""",
                (Jsonb([item.model_dump(mode="json") for item in observations]),
                 Jsonb(_safe_usage(usage)), artifact_id, attempt_id),
            )
            return cursor.fetchone() is not None

    def fail(self, artifact_id: UUID, attempt_id: UUID, code: str) -> None:
        with self.cases._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """update public.media_analysis_artifacts
                   set status='failed', lease_until=null, failure_count=failure_count+1,
                       failure_history=failure_history || jsonb_build_array(jsonb_build_object(
                           'attempt_id', %s::text, 'code', %s::text, 'at', now())),
                       last_error_code=%s, finished_at=now(), updated_at=now()
                   where id=%s and attempt_id=%s and status='running'""",
                (str(attempt_id), code, code, artifact_id, attempt_id),
            )

    def link(self, actor_id: UUID, case_id: UUID, evidence_id: UUID, artifact_id: UUID) -> bool:
        with self.cases._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from public.cases where id=%s and created_by_user_id=%s for update",
                (case_id, actor_id),
            )
            current = cursor.fetchone()
            if current is None:
                return False
            cursor.execute(
                """insert into public.case_media_analysis_links (case_id,evidence_id,artifact_id)
                   select e.case_id,e.id,a.id from public.evidence e
                   join public.cases c on c.id=e.case_id
                   join public.media_analysis_artifacts a
                     on a.sha256_verified=e.sha256_verified and a.scope=%s and a.status='succeeded'
                   where e.id=%s and e.case_id=%s and c.created_by_user_id=%s and a.id=%s
                   on conflict do nothing returning artifact_id""",
                (f"user:{actor_id}", evidence_id, case_id, actor_id, artifact_id),
            )
            if cursor.fetchone() is not None:
                cursor.execute("update public.approvals set superseded_at=now() where case_id=%s and superseded_at is null", (case_id,))
                cursor.execute(
                    """update public.cases set state_version=state_version+1,
                       content_revision=content_revision+1, status='collecting',
                       current_draft_id=null, updated_at=now() where id=%s returning *""",
                    (case_id,),
                )
                updated = cursor.fetchone()
                cursor.execute(
                    """insert into public.audit_events
                       (case_id, actor_user_id, event_type, state_version_before, state_version_after,
                        content_revision_before, content_revision_after, metadata_json)
                       values (%s,%s,'case.video_analysis_linked',%s,%s,%s,%s,%s)""",
                    (case_id, actor_id, current["state_version"], updated["state_version"],
                     current["content_revision"], updated["content_revision"],
                     Jsonb({"evidence_id": str(evidence_id), "artifact_id": str(artifact_id)})),
                )
                return True
            cursor.execute(
                """select 1 from public.case_media_analysis_links where case_id=%s
                   and evidence_id=%s and artifact_id=%s""",
                (case_id, evidence_id, artifact_id),
            )
            return cursor.fetchone() is not None


class VideoReuseService:
    def __init__(self, repository: PostgresVideoRepository, storage: StorageAdapter,
                 pipeline: VideoPipelineSpec, analyzer: VideoAnalyzer | None = None,
                 observation_validator: Callable[[Path, list[VideoObservation]], None] | None = None) -> None:
        self.repository = repository
        self.storage = storage
        self.pipeline = pipeline
        self.analyzer = analyzer
        self.observation_validator = observation_validator

    def run(self, actor_id: str, case_id: UUID, evidence_id: UUID,
            request: VideoAnalysisRequest) -> VideoAnalysisResponse:
        started = time.monotonic()
        actor = actor_uuid(actor_id)
        evidence = self.repository.owned_evidence(actor, case_id, evidence_id)
        if evidence is None:
            raise EvidenceNotFoundError
        if evidence.mime_type != "video/mp4" or evidence.byte_size > MAX_VIDEO_BYTES:
            raise ValueError("Only an MP4 evidence file of at most 10 MB can be analyzed.")
        digest = evidence.sha256_verified
        if evidence.checksum_status == "mismatch":
            raise VideoChecksumMismatch("The uploaded bytes did not match the declared checksum.")
        if digest is None:
            digest, size = self._digest(evidence.storage_path)
            digest = digest if size == evidence.byte_size else None
            evidence = self.repository.verify(actor, case_id, evidence_id, digest)
            digest = evidence.sha256_verified
        assert digest is not None
        scope = f"user:{actor}"
        state, artifact = self.repository.claim(scope, digest, self.pipeline, request.processing_policy)
        if state == "not_preprocessed":
            changed = self.repository.has_incompatible_artifact(scope, digest, self.pipeline.fingerprint)
            return self._response("not_preprocessed", None, evidence_id, [], False, started,
                                  pipeline_changed=changed)
        assert artifact is not None
        if state == "processing":
            return self._response("processing", artifact, evidence_id, [], False, started)
        if state == "reused":
            if not self.repository.link(actor, case_id, evidence_id, artifact["id"]):
                raise EvidenceNotFoundError
            observations = [VideoObservation.model_validate(value) for value in artifact["observations_json"]]
            return self._response("reused", artifact, evidence_id, observations, True, started)
        if self.analyzer is None:
            self.repository.fail(artifact["id"], artifact["attempt_id"], "analyzer_unavailable")
            raise VideoAnalyzerUnavailable("No video analyzer is configured for new processing.")
        try:
            with tempfile.TemporaryDirectory(prefix="claim-video-") as directory:
                path = Path(directory) / "original.mp4"
                downloaded_digest, downloaded_size = self._digest(evidence.storage_path, path)
                if downloaded_digest != digest or downloaded_size != evidence.byte_size:
                    raise VideoSourceChanged("The private object changed after checksum verification.")
                observations, usage = self.analyzer.analyze(path, self.pipeline)
                observations = [VideoObservation.model_validate(item) for item in observations]
                if self.observation_validator:
                    self.observation_validator(path, observations)
            if not self.repository.complete(artifact["id"], artifact["attempt_id"], observations, usage):
                return self._response("processing", artifact, evidence_id, [], False, started)
        except Exception as error:
            code = "source_changed" if isinstance(error, VideoSourceChanged) else "analysis_failed"
            self.repository.fail(artifact["id"], artifact["attempt_id"], code)
            raise
        if not self.repository.link(actor, case_id, evidence_id, artifact["id"]):
            raise EvidenceNotFoundError
        artifact = {**artifact, "usage_json": _safe_usage(usage)}
        return self._response("completed", artifact, evidence_id, observations, False, started)

    def _digest(self, storage_path: str, output: Path | None = None) -> tuple[str, int]:
        sha = hashlib.sha256()
        total = 0
        with output.open("wb") if output else nullcontext() as destination:
            for chunk in self.storage.read_object_chunks(storage_path):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise StorageAdapterError("Video exceeds the processing size limit.")
                sha.update(chunk)
                if destination is not None:
                    destination.write(chunk)
        return sha.hexdigest(), total

    def _response(self, status: Literal["reused", "completed", "processing", "not_preprocessed"],
                  artifact: dict[str, Any] | None, evidence_id: UUID,
                  observations: list[VideoObservation], reused: bool, started: float,
                  pipeline_changed: bool = False) -> VideoAnalysisResponse:
        saved = artifact.get("usage_json", {}).get("cost_minor") if reused and artifact else None
        result = VideoAnalysisResponse(
            status=status, artifact_id=artifact["id"] if artifact and status != "not_preprocessed" else None,
            pipeline_fingerprint=self.pipeline.fingerprint, analysis_reused=reused,
            observations=_source_observations(evidence_id, observations),
            estimated_cost_saved_minor=saved if isinstance(saved, int) else None,
            pipeline_changed=pipeline_changed,
        )
        if os.getenv("LOGFIRE_TOKEN", "").strip():
            import logfire
            logfire.info("video_analysis_reuse", analysis_reused=reused,
                         artifact_id=str(result.artifact_id) if result.artifact_id else None,
                         duration_ms=int((time.monotonic() - started) * 1000),
                         estimated_cost_saved_minor=result.estimated_cost_saved_minor,
                         status=status)
        return result
