"""Source-checked, offline counterparty lookup for an identified involved vehicle."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from psycopg.types.json import Jsonb

from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.mock_insurance import PATTERNS, SOURCE_VERSION, correspondent_lookup, insurance_lookup, normalize_plate
from claim_api.models import AnalysisSourceRef, CaseView, ContractModel
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.video_reuse import configured_pipeline, configured_vision_pipeline


class LookupRejectedError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class CounterpartyLookupRequest(ContractModel):
    plate_candidate: str = Field(min_length=1, max_length=32)
    country: str = Field(min_length=2, max_length=2)
    incident_date: date
    vehicle_track_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    supporting_source_refs: list[AnalysisSourceRef] = Field(min_length=1)
    identification_status: Literal["observed", "human_confirmed", "uncertain"]
    validation_reason: str | None = Field(default=None, max_length=1000)
    expected_state_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_confirmation(self) -> "CounterpartyLookupRequest":
        if self.identification_status == "human_confirmed" and not (self.validation_reason or "").strip():
            raise ValueError("Human confirmation requires a reason.")
        return self


def canonical_query(query: dict) -> tuple[str, str]:
    encoded = json.dumps(query, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _check_observations(cursor, case_id: UUID, request: CounterpartyLookupRequest, plate: str) -> None:
    """Only a persisted vision fact tied to this case and track can support selection."""
    cursor.execute("""select payload_json from public.provider_results
                      where case_id=%s and provider='vision' and status='matched'
                      and payload_json->>'pipeline_fingerprint'=%s""",
                   (case_id, configured_vision_pipeline().fingerprint))
    refs = {(ref.kind, ref.id, ref.locator) for ref in request.supporting_source_refs}
    matching = []
    found_refs = set()
    for row in cursor.fetchall():
        for observation in row["payload_json"].get("observations", []):
            source = observation.get("source_ref") or {}
            key = (source.get("kind"), source.get("id"), source.get("locator"))
            if key in refs and observation.get("vehicle_track_id") == request.vehicle_track_id:
                matching.append(observation)
                found_refs.add(key)
    cursor.execute("""select l.evidence_id,a.observations_json
                      from public.case_media_analysis_links l
                      join public.media_analysis_artifacts a on a.id=l.artifact_id
                      where l.case_id=%s and a.status='succeeded' and a.pipeline_fingerprint=%s""",
                   (case_id, configured_pipeline().fingerprint))
    for row in cursor.fetchall():
        for item in row["observations_json"] or []:
            key = ("evidence", str(row["evidence_id"]), f"video:{item.get('start_ms')}-{item.get('end_ms')}")
            if key in refs and item.get("vehicle_track_id") == request.vehicle_track_id:
                matching.append({**item, "category": "plate" if item.get("category") == "visible_text" else item.get("category"),
                                 "plate_candidate": item.get("plate_candidate")})
                found_refs.add(key)
    if found_refs != refs:
        raise LookupRejectedError("unsupported_association", "Every cited source must belong to the selected vehicle track in this case.")
    if not matching:
        raise LookupRejectedError("unsupported_association", "A case-local observation on the selected vehicle track is required.")
    plates = [item for item in matching if item.get("category") == "plate"]
    if not plates:
        raise LookupRejectedError("plate_source_required", "A cited plate observation on the selected track is required.")
    if request.identification_status == "observed":
        if not any(item.get("status") == "observed" and not item.get("uncertain_positions")
                   and normalize_plate(item.get("plate_candidate") or "") == plate for item in plates):
            raise LookupRejectedError("plate_not_observed", "The full plate is not observed on the selected vehicle.")
    elif request.identification_status == "uncertain":
        if not any(item.get("status") == "uncertain" and normalize_plate(item.get("plate_candidate") or "") == plate for item in plates):
            raise LookupRejectedError("plate_source_required", "The partial plate must match a cited uncertain reading.")
    else:
        # A handler may resolve an uncertain reading, but the known characters must agree.
        if not any(_compatible_partial(item.get("plate_candidate") or "", plate) for item in plates):
            raise LookupRejectedError("plate_conflicts_with_source", "The confirmed plate conflicts with the cited reading.")
    movement = [item for item in matching if item.get("category") == "movement"
                and item.get("status") == "observed"]
    if not movement:
        raise LookupRejectedError("involvement_source_required", "A movement observation on the selected track is required.")


def _compatible_partial(observed: str, selected: str) -> bool:
    candidate = normalize_plate(observed)
    return len(candidate) == len(selected) and all(a == "?" or a == b for a, b in zip(candidate, selected))


def current_result_ids(case: CaseView) -> set[UUID] | None:
    current = case.counterparty_lookup
    if current is None:
        return None
    if not current.current:
        return set()
    return {id for id in (current.insurance_result_id, current.correspondent_result_id) if id is not None}


class CounterpartyLookupService:
    def __init__(self, repository: PostgresCaseRepository) -> None:
        self.repository = repository

    def run(self, actor: str, case_id: UUID, request: CounterpartyLookupRequest) -> CaseView:
        actor_id = UUID(actor)
        country = request.country.strip().upper().replace("GB", "UK")
        plate = normalize_plate(request.plate_candidate)
        if country not in PATTERNS:
            raise LookupRejectedError("unsupported_country", "Only FR or UK mock plates are supported.")
        complete = bool(re.fullmatch(PATTERNS[country], plate))
        partial = bool("?" in plate and re.fullmatch(r"[A-Z0-9?]+", plate) and len(plate) == 7)
        if not complete and not (partial and request.identification_status == "uncertain"):
            raise LookupRejectedError("plate_incomplete_or_invalid", "A complete FR or UK plate is required.")
        if complete and request.identification_status == "uncertain":
            raise LookupRejectedError("plate_incomplete_or_invalid", "Uncertain status requires an incomplete plate.")
        with self.repository._connection() as connection, connection.cursor() as cursor:
            cursor.execute("select * from public.cases where id=%s and created_by_user_id=%s for update", (case_id, actor_id))
            case = cursor.fetchone()
            if case is None:
                raise CaseNotFoundError
            if case["state_version"] != request.expected_state_version:
                raise StaleCaseError(case["state_version"])
            intake = case["intake_json"]
            incident_at = intake.get("incident_at")
            if not incident_at or date.fromisoformat(incident_at[:10]) != request.incident_date:
                raise LookupRejectedError("incident_date_mismatch", "Use the case's accident date.")
            if normalize_plate(intake.get("insured_plate") or "") == plate:
                raise LookupRejectedError("insured_vehicle", "The selected plate belongs to the insured vehicle.")
            _check_observations(cursor, case_id, request, plate)
            source_refs = sorted((ref.model_dump() for ref in request.supporting_source_refs),
                                 key=lambda ref: (ref["kind"], ref["id"], ref["locator"]))
            association_query = {
                "plate_candidate": plate, "country": country,
                "incident_date": request.incident_date.isoformat(),
                "vehicle_track_id": request.vehicle_track_id,
                "identification_status": request.identification_status,
                "supporting_source_refs": source_refs,
                "validation_reason": request.validation_reason.strip() if request.validation_reason else None,
            }
            insurance = insurance_lookup(plate, country, request.incident_date)
            insurance_query = {**insurance.query, **association_query}
            insurance_id, insurance_created = self._persist(cursor, case_id, insurance, insurance_query)
            correspondent_id = None
            correspondent_created = False
            if insurance.status == "matched" and country == "UK":
                correspondent = correspondent_lookup(insurance.data["insurer_id"], "FR", request.incident_date)
                correspondent_query = {**correspondent.query, "insurance_result_id": str(insurance_id)}
                correspondent_id, correspondent_created = self._persist(cursor, case_id, correspondent, correspondent_query)
            cursor.execute("select * from public.case_counterparty_lookups where case_id=%s for update", (case_id,))
            old = cursor.fetchone()
            same = bool(old and old["active"] and old["insurance_result_id"] == insurance_id
                        and old["correspondent_result_id"] == correspondent_id)
            if same:
                return self.repository._case_view(cursor, case)
            cursor.execute("""insert into public.case_counterparty_lookups
                (case_id,plate_candidate,country,incident_date,vehicle_track_id,identification_status,
                 supporting_source_refs_json,validation_reason,validated_by_user_id,validated_at,
                 insurance_result_id,correspondent_result_id)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,case when %s='human_confirmed' then now() else null end,%s,%s)
                on conflict (case_id) do update set plate_candidate=excluded.plate_candidate,
                country=excluded.country,incident_date=excluded.incident_date,
                vehicle_track_id=excluded.vehicle_track_id,identification_status=excluded.identification_status,
                supporting_source_refs_json=excluded.supporting_source_refs_json,
                validation_reason=excluded.validation_reason,validated_by_user_id=excluded.validated_by_user_id,
                validated_at=excluded.validated_at,insurance_result_id=excluded.insurance_result_id,
                correspondent_result_id=excluded.correspondent_result_id,active=true,updated_at=now()""",
                (case_id, plate, country, request.incident_date, request.vehicle_track_id,
                 request.identification_status, Jsonb(source_refs), request.validation_reason,
                 actor_id if request.identification_status == "human_confirmed" else None,
                 request.identification_status, insurance_id, correspondent_id))
            cursor.execute("""update public.cases set state_version=state_version+1,
                content_revision=content_revision+1,status='collecting',current_draft_id=null,updated_at=now()
                where id=%s returning *""", (case_id,))
            updated = cursor.fetchone()
            cursor.execute("update public.approvals set superseded_at=now() where case_id=%s and superseded_at is null", (case_id,))
            cursor.execute("""insert into public.audit_events
                (case_id,actor_user_id,event_type,state_version_before,state_version_after,
                 content_revision_before,content_revision_after,metadata_json)
                values (%s,%s,'case.counterparty_lookup',%s,%s,%s,%s,%s)""",
                (case_id, actor_id, case["state_version"], updated["state_version"],
                 case["content_revision"], updated["content_revision"], Jsonb({
                    "insurance_result_id": str(insurance_id),
                    "correspondent_result_id": str(correspondent_id) if correspondent_id else None,
                    "previous_insurance_result_id": str(old["insurance_result_id"]) if old else None,
                    "identification_status": request.identification_status,
                    "supporting_source_refs": source_refs,
                    "validation_reason": request.validation_reason,
                    "validated_by": str(actor_id) if request.identification_status == "human_confirmed" else None,
                    "provider_result_created": insurance_created or correspondent_created,
                })))
            return self.repository._case_view(cursor, updated)

    @staticmethod
    def _persist(cursor, case_id, result, query):
        _, query_hash = canonical_query(query)
        cursor.execute("""insert into public.provider_results
            (case_id,provider,mode,status,query_hash,source_version,query_json,payload_json,reason)
            values (%s,%s,'mock',%s,%s,%s,%s,%s,%s)
            on conflict (case_id,provider,mode,query_hash,source_version) do nothing
            returning id""", (case_id, result.provider, result.status, query_hash, SOURCE_VERSION,
                              Jsonb(query), Jsonb(result.data), result.reason))
        row = cursor.fetchone()
        if row:
            return row["id"], True
        cursor.execute("""select id from public.provider_results where case_id=%s and provider=%s
            and mode='mock' and query_hash=%s and source_version=%s""",
            (case_id, result.provider, query_hash, SOURCE_VERSION))
        return cursor.fetchone()["id"], False
