"""Case-local visual observations. Analyzer inputs never contain case labels or narrative."""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator
from psycopg.types.json import Jsonb

from claim_api.evidence_service import EvidenceNotFoundError, actor_uuid
from claim_api.models import AnalysisSourceRef, ContractModel, EvidenceView
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.storage import StorageAdapter, StorageAdapterError, StorageObjectNotFound, StorageUnavailableError
from claim_api.video_reuse import (
    PostgresVideoRepository, VideoAnalysisRequest, VideoReuseService,
    VideoAnalyzerUnavailable, VideoChecksumMismatch, VideoObservation,
    VideoPipelineSpec, VideoSourceChanged, configured_pipeline,
)


PHOTO_LIMIT = 10_000_000
VISION_VERSION = "vision-observation-v1"
VisionStatus = Literal["observed", "uncertain", "not_visible", "processing", "not_preprocessed", "unavailable", "error"]


class UnsupportedFixtureError(RuntimeError):
    pass


class ImageZone(ContractModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def within_image(self) -> "ImageZone":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Image zone must remain inside the image.")
        return self


class VisualFact(ContractModel):
    category: Literal["vehicle", "plate", "damage", "movement", "other"]
    status: Literal["observed", "uncertain", "not_visible"]
    text: str = Field(min_length=1, max_length=1000)
    vehicle_track_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    plate_candidate: str | None = Field(default=None, max_length=32)
    uncertain_positions: list[int] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    calibration: str | None = Field(default=None, max_length=100)
    zone: ImageZone | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_locator_and_uncertainty(self) -> "VisualFact":
        if (self.zone is None) == (self.start_ms is None):
            raise ValueError("Exactly one image zone or video time range is required.")
        if self.start_ms is not None and (self.end_ms is None or self.end_ms < self.start_ms):
            raise ValueError("Video time range is invalid.")
        if self.plate_candidate is None and self.uncertain_positions:
            raise ValueError("Uncertain plate positions require a plate candidate.")
        if self.plate_candidate is not None:
            if len(set(self.uncertain_positions)) != len(self.uncertain_positions):
                raise ValueError("Uncertain plate positions must be unique.")
            if any(index < 0 or index >= len(self.plate_candidate) for index in self.uncertain_positions):
                raise ValueError("Uncertain plate position is outside the candidate.")
            if any(index not in self.uncertain_positions for index, char in enumerate(self.plate_candidate) if char == "?"):
                raise ValueError("Unknown plate characters must be marked uncertain.")
            if self.status == "observed" and self.uncertain_positions:
                raise ValueError("An uncertain plate cannot have observed status.")
        return self

    @property
    def locator(self) -> str:
        if self.zone:
            return "image:" + ",".join(f"{value:.4f}" for value in (
                self.zone.x, self.zone.y, self.zone.width, self.zone.height))
        return f"video:{self.start_ms}-{self.end_ms}"


class SourcedVisualFact(VisualFact):
    media_id: UUID
    source_ref: AnalysisSourceRef


class VisionRequest(ContractModel):
    evidence_id: UUID
    processing_policy: Literal["reuse_only", "allow_new"] = "reuse_only"


class VisionResponse(ContractModel):
    status: VisionStatus
    provider_result_id: UUID | None = None
    mode: Literal["mock", "live"] | None = None
    source_version: str = VISION_VERSION
    observations: list[SourcedVisualFact] = Field(default_factory=list)
    reason: str | None = None
    analysis_reused: bool = False


class EvidenceAnalyzer(Protocol):
    mode: Literal["mock", "live"]
    def analyze_image(self, image_path: Path, mime_type: str, pipeline: VideoPipelineSpec) -> list[VisualFact]: ...


def source_fact(evidence_id: UUID, fact: VisualFact) -> SourcedVisualFact:
    return SourcedVisualFact(**fact.model_dump(), media_id=evidence_id,
        source_ref=AnalysisSourceRef(kind="evidence", id=str(evidence_id), locator=fact.locator))


def validate_facts(evidence_id: UUID, mime_type: str, facts: list[VisualFact],
                   duration_ms: int | None = None) -> list[SourcedVisualFact]:
    result: list[SourcedVisualFact] = []
    for raw in facts:
        fact = VisualFact.model_validate(raw)
        if mime_type.startswith("image/") and fact.zone is None:
            raise ValueError("Image observation requires an image zone.")
        if mime_type.startswith("video/"):
            if fact.start_ms is None or duration_ms is None or fact.end_ms is None or fact.end_ms > duration_ms:
                raise ValueError("Video observation timecode exceeds verified duration.")
        result.append(source_fact(evidence_id, fact))
    return result


def visual_status(facts: list[SourcedVisualFact]) -> Literal["observed", "uncertain", "not_visible"]:
    if not facts:
        raise ValueError("An empty visual result is not a verified absence of visible facts.")
    if any(item.status == "uncertain" for item in facts):
        return "uncertain"
    if any(item.status == "observed" for item in facts):
        return "observed"
    return "not_visible"


def mp4_duration_ms(path: Path) -> int:
    """Read the MP4 movie header; reject missing or malformed duration metadata."""
    data = path.read_bytes()

    def boxes(start: int, end: int):
        offset = start
        while offset + 8 <= end:
            size = int.from_bytes(data[offset:offset + 4], "big")
            kind = data[offset + 4:offset + 8]
            header = 8
            if size == 1:
                if offset + 16 > end:
                    raise ValueError("Invalid MP4 box header.")
                size = int.from_bytes(data[offset + 8:offset + 16], "big")
                header = 16
            elif size == 0:
                size = end - offset
            if size < header or offset + size > end:
                raise ValueError("Invalid MP4 box size.")
            yield kind, offset + header, offset + size
            offset += size

    for kind, start, end in boxes(0, len(data)):
        if kind != b"moov":
            continue
        for subkind, substart, subend in boxes(start, end):
            if subkind != b"mvhd":
                continue
            version = data[substart]
            if version == 0 and subend - substart >= 20:
                scale = int.from_bytes(data[substart + 12:substart + 16], "big")
                duration = int.from_bytes(data[substart + 16:substart + 20], "big")
            elif version == 1 and subend - substart >= 32:
                scale = int.from_bytes(data[substart + 20:substart + 24], "big")
                duration = int.from_bytes(data[substart + 24:substart + 32], "big")
            else:
                raise ValueError("Unsupported MP4 movie header.")
            if scale <= 0 or duration <= 0 or duration in {2**32 - 1, 2**64 - 1}:
                raise ValueError("MP4 duration is unavailable.")
            return duration * 1000 // scale
    raise ValueError("MP4 movie duration is unavailable.")


def validate_video_observations(path: Path, observations: list[VideoObservation]) -> None:
    duration = mp4_duration_ms(path)
    if not observations:
        raise ValueError("An empty video result requires an explicit not-visible observation.")
    for item in observations:
        if item.end_ms > duration:
            raise ValueError("Video observation timecode exceeds verified duration.")
        VisualFact(category="plate" if item.category == "visible_text" else item.category,
                   status=item.status, text=item.description,
                   start_ms=item.start_ms, end_ms=item.end_ms,
                   vehicle_track_id=item.vehicle_track_id, plate_candidate=item.plate_candidate,
                   uncertain_positions=item.uncertain_positions, confidence=item.confidence,
                   calibration=item.calibration)


class PostgresVisionRepository:
    def __init__(self, cases: PostgresCaseRepository) -> None:
        self.cases = cases
        self.artifacts = PostgresVideoRepository(cases)

    def owned_evidence(self, actor: UUID, case_id: UUID, evidence_id: UUID) -> EvidenceView | None:
        return self.cases.get_owned_evidence(actor, case_id, evidence_id)

    def save(self, actor: UUID, case_id: UUID, evidence_id: UUID, mode: Literal["mock", "live"],
             status: VisionStatus, fingerprint: str,
             observations: list[SourcedVisualFact], reason: str | None) -> UUID:
        key = hashlib.sha256(f"{evidence_id}:{fingerprint}".encode()).hexdigest()
        payload = {"evidence_id": str(evidence_id), "observations": [x.model_dump(mode="json") for x in observations],
                   "pipeline_fingerprint": fingerprint}
        db_status = "matched" if status in {"observed", "uncertain", "not_visible"} else status
        with self.cases._connection() as connection, connection.cursor() as cursor:
            cursor.execute("select * from public.cases where id=%s and created_by_user_id=%s for update", (case_id, actor))
            current = cursor.fetchone()
            if current is None:
                raise EvidenceNotFoundError
            cursor.execute("select 1 from public.evidence where id=%s and case_id=%s", (evidence_id, case_id))
            if cursor.fetchone() is None:
                raise EvidenceNotFoundError
            cursor.execute("""select * from public.provider_results where case_id=%s and provider='vision'
                and mode=%s and query_hash=%s and source_version=%s for update""",
                (case_id, mode, key, VISION_VERSION))
            old = cursor.fetchone()
            if old and old["status"] == db_status and old["payload_json"] == payload and old["reason"] == reason:
                return old["id"]
            cursor.execute("""insert into public.provider_results
                (case_id, provider, mode, status, query_hash, source_version, payload_json, reason)
                values (%s,'vision',%s,%s,%s,%s,%s,%s)
                on conflict (case_id,provider,mode,query_hash,source_version) do update
                set status=excluded.status, payload_json=excluded.payload_json, reason=excluded.reason,
                    retrieved_at=now() returning id""",
                (case_id, mode, db_status, key, VISION_VERSION, Jsonb(payload), reason))
            result_id = cursor.fetchone()["id"]
            cursor.execute("update public.approvals set superseded_at=now() where case_id=%s and superseded_at is null", (case_id,))
            cursor.execute("""update public.cases set state_version=state_version+1,
                content_revision=content_revision+1, status='collecting', current_draft_id=null,
                updated_at=now() where id=%s""", (case_id,))
            cursor.execute("""insert into public.audit_events
                (case_id,actor_user_id,event_type,state_version_before,state_version_after,
                 content_revision_before,content_revision_after,metadata_json)
                values (%s,%s,'case.vision_result_saved',%s,%s,%s,%s,%s)""",
                (case_id, actor, current["state_version"], current["state_version"] + 1,
                 current["content_revision"], current["content_revision"] + 1,
                 Jsonb({"evidence_id": str(evidence_id), "provider_result_id": str(result_id), "status": status})))
            return result_id


class VisionService:
    def __init__(self, repository: PostgresVisionRepository, storage: StorageAdapter,
                 analyzer: EvidenceAnalyzer | None = None, video: VideoReuseService | None = None,
                 pipeline: VideoPipelineSpec | None = None) -> None:
        self.repository = repository
        self.storage = storage
        self.analyzer = analyzer
        self.video = video
        self.pipeline = pipeline or configured_pipeline()

    def run(self, actor_id: str, case_id: UUID, request: VisionRequest) -> VisionResponse:
        actor = actor_uuid(actor_id)
        evidence = self.repository.owned_evidence(actor, case_id, request.evidence_id)
        if evidence is None:
            raise EvidenceNotFoundError
        if evidence.mime_type == "video/mp4":
            return self._video(actor_id, actor, case_id, evidence, request.processing_policy)
        if evidence.mime_type not in {"image/png", "image/jpeg", "image/webp"} or evidence.byte_size > PHOTO_LIMIT:
            raise ValueError("Only supported images of at most 10 MB can be analyzed.")
        try:
            return self._photo(actor, case_id, evidence, request.processing_policy)
        except (StorageAdapterError, StorageObjectNotFound, StorageUnavailableError):
            return self._save(actor, case_id, evidence, "unavailable", [], "storage_unavailable")
        except UnsupportedFixtureError:
            return self._save(actor, case_id, evidence, "unavailable", [], "fixture_not_supported")
        except ValueError:
            return self._save(actor, case_id, evidence, "error", [], "invalid_visual_result")
        except Exception:
            return self._save(actor, case_id, evidence, "error", [], "analysis_failed")

    def _photo(self, actor: UUID, case_id: UUID, evidence: EvidenceView,
               policy: Literal["reuse_only", "allow_new"]) -> VisionResponse:
        cache = self.repository.artifacts
        if evidence.checksum_status == "mismatch":
            raise ValueError("The uploaded bytes did not match the declared checksum.")
        # A client-declared hash must never suffice to reuse an analysis.
        digest = evidence.sha256_verified
        with TemporaryDirectory(prefix="claim-vision-") as directory:
            path = Path(directory) / "original"
            if digest is None:
                digest = self._download_photo(evidence, path)
                evidence = cache.verify(actor, case_id, evidence.id, digest)
            # Separate photo output from the video schema, including its MIME input.
            scope = f"user:{actor}:image:{evidence.mime_type}"
            state, artifact = cache.claim(scope, digest, self.pipeline, policy)
            if state in {"not_preprocessed", "processing"}:
                return VisionResponse(status=state, reason=state)
            assert artifact is not None
            if state == "reused":
                raw = [VisualFact.model_validate(value) for value in artifact["observations_json"]]
                facts = validate_facts(evidence.id, evidence.mime_type, raw)
                return self._save(actor, case_id, evidence, visual_status(facts), facts, None, reused=True)
            if self.analyzer is None:
                cache.fail(artifact["id"], artifact["attempt_id"], "analyzer_unavailable")
                return self._save(actor, case_id, evidence, "unavailable", [], "analyzer_unavailable")
            try:
                if not path.exists():
                    self._download_photo(evidence, path)
                raw = [VisualFact.model_validate(value) for value in
                       self.analyzer.analyze_image(path, evidence.mime_type, self.pipeline)]
                facts = validate_facts(evidence.id, evidence.mime_type, raw)
                status = visual_status(facts)
                # Save only intrinsic facts; bind IDs to the current case on every hit.
                if not cache.complete(artifact["id"], artifact["attempt_id"], raw, {}):
                    return VisionResponse(status="processing", reason="processing")
            except Exception:
                cache.fail(artifact["id"], artifact["attempt_id"], "analysis_failed")
                raise
        return self._save(actor, case_id, evidence, status, facts, None)

    def _download_photo(self, evidence: EvidenceView, path: Path) -> str:
        digest = hashlib.sha256()
        size = 0
        with path.open("wb") as target:
            for chunk in self.storage.read_object_chunks(evidence.storage_path):
                size += len(chunk)
                if size > PHOTO_LIMIT:
                    raise StorageAdapterError("Image exceeds the processing size limit.")
                target.write(chunk)
                digest.update(chunk)
        expected_digest = evidence.sha256_verified or evidence.client_sha256
        if size != evidence.byte_size or (expected_digest and digest.hexdigest() != expected_digest):
            raise ValueError("The private image differs from the recorded evidence.")
        return digest.hexdigest()

    def _video(self, actor_id: str, actor: UUID, case_id: UUID, evidence: EvidenceView,
               policy: Literal["reuse_only", "allow_new"]) -> VisionResponse:
        if self.video is None:
            return VisionResponse(status="unavailable", reason="Video reuse service is unavailable.")
        try:
            result = self.video.run(actor_id, case_id, evidence.id, VideoAnalysisRequest(processing_policy=policy))
        except VideoAnalyzerUnavailable:
            return self._save(actor, case_id, evidence, "unavailable", [], "analyzer_unavailable")
        except (StorageAdapterError, StorageObjectNotFound, StorageUnavailableError):
            return self._save(actor, case_id, evidence, "unavailable", [], "storage_unavailable")
        except UnsupportedFixtureError:
            return self._save(actor, case_id, evidence, "unavailable", [], "fixture_not_supported")
        except (VideoChecksumMismatch, VideoSourceChanged):
            return self._save(actor, case_id, evidence, "error", [], "video_source_invalid")
        except ValueError:
            return self._save(actor, case_id, evidence, "error", [], "invalid_visual_result")
        except RuntimeError:
            return self._save(actor, case_id, evidence, "error", [], "analysis_failed")
        if result.status in {"processing", "not_preprocessed"}:
            return VisionResponse(status=result.status, reason=result.status, analysis_reused=result.analysis_reused)
        try:
            facts = [source_fact(evidence.id, VisualFact(
                category="plate" if item.observation.category == "visible_text" else item.observation.category,
                status=item.observation.status,
                text=item.observation.description, start_ms=item.observation.start_ms,
                end_ms=item.observation.end_ms, vehicle_track_id=item.observation.vehicle_track_id,
                plate_candidate=item.observation.plate_candidate,
                uncertain_positions=item.observation.uncertain_positions,
                confidence=item.observation.confidence, calibration=item.observation.calibration,
            )) for item in result.observations]
            status = visual_status(facts)
        except ValueError:
            return self._save(actor, case_id, evidence, "error", [], "invalid_visual_result")
        return self._save(actor, case_id, evidence, status, facts, None,
                          reused=result.analysis_reused)

    def _save(self, actor: UUID, case_id: UUID, evidence: EvidenceView,
              status: VisionStatus, facts: list[SourcedVisualFact],
              reason: str | None, reused: bool = False) -> VisionResponse:
        adapter = self.analyzer if evidence.mime_type.startswith("image/") else getattr(self.video, "analyzer", None)
        mode: Literal["mock", "live"] = adapter.mode if adapter and hasattr(adapter, "mode") else "live"
        result_id = self.repository.save(actor, case_id, evidence.id, mode, status,
                                         self.pipeline.fingerprint, facts, reason)
        return VisionResponse(status=status, provider_result_id=result_id, mode=mode,
                              observations=facts, reason=reason, analysis_reused=reused)
