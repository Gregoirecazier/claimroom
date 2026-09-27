"""Versioned, provider-neutral voice intake boundary.

The voice agent supplies candidate facts with an exact caller excerpt.  The
server validates provenance and performs triage; it never asks a model to fill
an absent field or treats a caller statement as verified evidence.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from claim_api.fixtures import missing_intake_fields
from claim_api.models import Intake
from claim_api.voice_normalization import minutes_ago, normalize_address


class VoiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


VoiceFieldName = Literal[
    "narrative", "location", "incident_time", "third_party_involved",
    "third_party_presence", "danger_status", "injury_severity", "stationary",
    "insured_name", "insured_reference", "policy_reference", "insured_plate",
    "insured_vehicle", "vehicle_color", "damage_description",
]


class TranscriptSegmentV1(VoiceModel):
    id: str = Field(min_length=1, max_length=80)
    speaker: Literal["caller", "assistant"]
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    text: str = Field(min_length=1, max_length=4000)
    observed_at: datetime | None = None

    @field_validator("observed_at")
    @classmethod
    def utc_observed_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def valid_span(self) -> "TranscriptSegmentV1":
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise ValueError("Transcript segment end_ms precedes start_ms")
        return self


class VoiceFactV1(VoiceModel):
    field: VoiceFieldName
    value: str = Field(min_length=1, max_length=4000)
    segment_id: str = Field(min_length=1, max_length=80)
    excerpt: str = Field(min_length=1, max_length=1000)
    uncertainty: Literal["explicit", "inferred", "uncertain"] = "explicit"


class VoiceIntakeRequestV1(VoiceModel):
    schema_version: Literal[1]
    provider: Literal["vapi"] = "vapi"
    telephony_provider: Literal["twilio", "web"] = "twilio"
    speech_provider: Literal["gradium"] = "gradium"
    provider_call_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=1)
    source_event_key: str = Field(min_length=1, max_length=200)
    event_type: Literal["call_started", "final_turn", "end_of_call_report", "call_failed"] = "final_turn"
    call_started_at: datetime
    received_at: datetime
    assistant_id: str = Field(min_length=1, max_length=200)
    assistant_version: str = Field(min_length=1, max_length=120)
    extractor_version: str = Field(min_length=1, max_length=120)
    segments: list[TranscriptSegmentV1] = Field(default_factory=list, max_length=100)
    facts: list[VoiceFactV1] = Field(default_factory=list, max_length=30)
    mode: Literal["mock", "live"] = "mock"

    @field_validator("call_started_at", "received_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("call_started_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_sources(self) -> "VoiceIntakeRequestV1":
        if self.event_type == "final_turn" and not self.segments:
            raise ValueError("A final turn requires final transcript segments")
        if self.event_type == "call_started" and (self.segments or self.facts):
            raise ValueError("Call initialization cannot contain transcript or facts")
        by_id = {segment.id: segment for segment in self.segments}
        if len(by_id) != len(self.segments):
            raise ValueError("Transcript segment IDs must be unique")
        known_starts = [segment.start_ms for segment in self.segments if segment.start_ms is not None]
        if known_starts != sorted(known_starts):
            raise ValueError("Transcript segments must be in start-time order")
        if len({fact.field for fact in self.facts}) != len(self.facts):
            raise ValueError("Only one current fact per field is allowed")
        for fact in self.facts:
            segment = by_id.get(fact.segment_id)
            if segment is None or segment.speaker != "caller" or fact.excerpt not in segment.text:
                raise ValueError(f"{fact.field} needs an exact excerpt from a caller segment")
        return self


class VoiceTriageV1(VoiceModel):
    schema_version: Literal[1] = 1
    status: Literal["urgent_human_handoff", "collecting", "complete", "incomplete", "error"]
    missing_p0: list[str]
    next_question: str | None
    reason_codes: list[str]


class VoiceSessionView(VoiceModel):
    schema_version: Literal[1] = 1
    case_id: UUID
    provider: Literal["vapi"]
    provider_call_id: str
    sequence: int
    mode: Literal["mock", "live"]
    telephony_provider: Literal["twilio", "web"] = "twilio"
    triage: VoiceTriageV1
    facts: list[VoiceFactV1]
    call_started_at: datetime
    assistant_version: str
    assistant_id: str
    extractor_version: str


class VoiceIngestResponse(VoiceModel):
    session: VoiceSessionView
    replayed: bool


_QUESTION_PROTOCOL = yaml.safe_load(
    (Path(__file__).parent / "prompts/voice-questions-v3.yaml").read_text(encoding="utf-8"))
if (_QUESTION_PROTOCOL["schema_version"] != 1
        or set(_QUESTION_PROTOCOL["questions"]) != set(_QUESTION_PROTOCOL["required_order"])):
    raise ValueError("Voice question protocol is invalid")
QUESTIONS: dict[str, str] = _QUESTION_PROTOCOL["questions"]
REQUIRED_ORDER: tuple[str, ...] = tuple(_QUESTION_PROTOCOL["required_order"])

_JUST_NOW = re.compile(
    r"\b(?:viens\s+d['’]avoir|vient\s+de\s+se\s+produire|"
    r"(?:ça\s+)?vient\s+(?:juste\s+)?d['’]arriver|"
    r"à\s+l['’]instant|juste\s+maintenant|just\s+happened)\b", re.I,
)
_UNKNOWN = re.compile(r"\b(?:ne\s+sais\s+pas|sais\s+pas|aucune\s+idée|ne\s+connais\s+pas|unknown)\b", re.I)
_OTHER_VEHICLE_COLLISION = re.compile(
    r"(?:\b(?:une|un|la|le|autre)\s+(?:voiture|véhicule|auto|camion)\b"
    r".{0,100}\b(?:me|nous|m['’]est)\s+(?:rentr\w*\s+dedans|percut\w*|heurt\w*|tap\w*)"
    r"|\b(?:me|nous)\s+(?:faire|fait|faite)\s+"
    r"(?:rentr\w*\s+dedans|percut\w*|heurt\w*|tap\w*)\s+par\s+"
    r"(?:une|un|la|le)\s+(?:voiture|véhicule|auto|camion)\b)",
    re.I,
)
_AMBIGUOUS_PLACE = re.compile(r"\bplaces\s+(?:de|des|du|d['’]|à)\s*[^,.!?;]{2,70}", re.I)


def _assert_fact_supported(fact: VoiceFactV1) -> None:
    excerpt = fact.excerpt.casefold().strip()
    value = fact.value.casefold().strip()
    if fact.field in {"narrative", "insured_name", "insured_reference", "policy_reference",
                      "insured_plate", "insured_vehicle",
                      "vehicle_color", "damage_description"} and value not in excerpt:
        raise ValueError(f"{fact.field} must be quoted verbatim from the caller")
    if fact.field in {"location", "incident_time"} and value == "unknown":
        if not _UNKNOWN.search(fact.excerpt):
            raise ValueError(f"Unknown {fact.field} is not supported by the caller excerpt")
        return
    if (fact.field == "location" and value not in excerpt
            and (normalize_address(fact.value) is None
                 or normalize_address(fact.value) != normalize_address(fact.excerpt))):
        raise ValueError("location must be quoted or normalized from the caller")
    if fact.field == "incident_time":
        if value == "just_now":
            if not _JUST_NOW.search(fact.excerpt):
                raise ValueError("just_now needs an explicit recency phrase")
        elif value.startswith("minutes_ago:"):
            stated_minutes = minutes_ago(fact.excerpt)
            if stated_minutes is None or value != f"minutes_ago:{stated_minutes}":
                raise ValueError("Relative incident time is not supported by the caller excerpt")
        else:
            try:
                parsed = datetime.fromisoformat(fact.value)
            except ValueError as error:
                raise ValueError("incident_time must be an ISO timestamp or just_now") from error
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("incident_time must include a timezone")
            local_date = parsed.date().isoformat()
            hour = parsed.hour
            minute = parsed.minute
            explicit_clock = re.search(rf"\b0?{hour}(?:[:h]0?{minute})(?!\d)", excerpt)
            if local_date not in excerpt or not explicit_clock:
                raise ValueError("Incident timestamp requires explicit date and clock time in the caller excerpt")
    if fact.field == "danger_status":
        negative = bool(re.search(r"pas de danger|aucun danger|sans danger|(?<!pas )en sécurité|no danger|(?<!not )safe", excerpt))
        positive = bool(re.search(r"en danger|danger immédiat|risque immédiat|pas en sécurité|not safe|unsafe", excerpt))
        unknown = bool(re.search(r"ne sais pas|sais pas|aucune idée|unknown|incertain", excerpt))
        if value == "yes" and (not positive or negative):
            raise ValueError("Immediate danger is not supported by the caller excerpt")
        if value == "no" and (not negative or positive):
            raise ValueError("Absence of danger is not supported by the caller excerpt")
        if value == "unknown" and not unknown:
            raise ValueError("Unknown danger status is not supported by the caller excerpt")
    if fact.field == "injury_severity":
        none = bool(re.search(r"pas de bless|aucun bless|personne n[' ]est bless|personne de bless|no injur", excerpt))
        serious = bool(re.search(r"blessure grave|blessé grave|gravement bless|serious injur", excerpt))
        minor = bool(re.search(r"blessure légère|légèrement bless|petite bless|minor injur", excerpt))
        unknown = bool(re.search(r"ne sais pas|sais pas|aucune idée|unknown|incertain", excerpt))
        supported = {"none": none and not serious, "serious": serious and not none,
                     "minor": minor and not serious, "unknown": unknown}
        if not supported.get(value, False):
            raise ValueError("Injury severity is not supported by the caller excerpt")
    if fact.field == "third_party_involved":
        none = bool(re.search(r"pas d[' ]autre véhicule|aucun autre véhicule|pas de tiers|no other vehicle", excerpt))
        yes = bool(re.search(r"autre véhicule|autre voiture|véhicule tiers|other vehicle", excerpt)
                   or _OTHER_VEHICLE_COLLISION.search(excerpt)) and not none
        unknown = bool(re.search(r"ne sais pas|sais pas|aucune idée|unknown|incertain", excerpt))
        if not {"yes": yes, "no": none, "unknown": unknown}.get(value, False):
            raise ValueError("Third-party involvement is not supported by the caller excerpt")
    if fact.field == "third_party_presence":
        left = bool(re.search(r"est parti|a pris la fuite|s'est enfui|n'est pas resté|left", excerpt))
        present = bool(re.search(r"est resté|sur place|est présent|remained", excerpt)) and not left
        unknown = bool(re.search(r"ne sais pas|sais pas|aucune idée|unknown|incertain", excerpt))
        if not {"left": left, "present": present, "unknown": unknown}.get(value, False):
            raise ValueError("Third-party presence is not supported by the caller excerpt")


def _value(facts: dict[str, VoiceFactV1], name: str) -> str | None:
    item = facts.get(name)
    return item.value.strip() if item else None


def _choice(facts: dict[str, VoiceFactV1], name: str, allowed: set[str]) -> str | None:
    value = _value(facts, name)
    if value is None:
        return None
    if value not in allowed:
        raise ValueError(f"Invalid {name}: {value}")
    return value


def restore_segment_time(segment: TranscriptSegmentV1, call_started_at: datetime) -> TranscriptSegmentV1:
    """Anchor a legacy transcript offset before interpreting relative time."""
    if segment.observed_at is None and segment.start_ms is not None:
        return segment.model_copy(update={
            "observed_at": call_started_at + timedelta(milliseconds=segment.start_ms),
        })
    return segment


def deterministic_facts(segment: TranscriptSegmentV1) -> list[VoiceFactV1]:
    """Extract unambiguous caller facts from one final turn.

    Richer candidates may arrive through the Vapi tool, but the server checks
    each against this same persisted caller segment before projection.
    """
    text = segment.text[:1000]
    lowered = text.casefold()
    candidates: list[tuple[str, str, str]] = []
    if re.search(r"accident|heurt|percut|collision|choc|rentr\w*\s+dedans|embouti", lowered):
        candidates.append(("narrative", text, "explicit"))
    elapsed_minutes = minutes_ago(text)
    if elapsed_minutes is not None and segment.observed_at is not None:
        candidates.append(("incident_time", f"minutes_ago:{elapsed_minutes}", "inferred"))
    elif _JUST_NOW.search(text):
        candidates.append(("incident_time", "just_now", "inferred"))
    address = normalize_address(text)
    if address:
        candidates.append(("location", address, "explicit"))
    elif place := _AMBIGUOUS_PLACE.search(text):
        candidates.append(("location", place.group(0).strip(), "uncertain"))
    if re.search(r"en danger|danger immédiat|risque immédiat|pas en sécurité", lowered):
        candidates.append(("danger_status", "yes", "explicit"))
    elif re.search(r"pas de danger|aucun danger|sans danger|(?<!pas )en sécurité", lowered):
        candidates.append(("danger_status", "no", "explicit"))
    if re.search(r"blessure grave|blessé grave|gravement bless", lowered):
        candidates.append(("injury_severity", "serious", "explicit"))
    elif re.search(r"pas de bless|aucun bless|personne n[' ]est bless", lowered):
        candidates.append(("injury_severity", "none", "explicit"))
    elif re.search(r"blessure légère|légèrement bless|petite bless", lowered):
        candidates.append(("injury_severity", "minor", "explicit"))
    if re.search(r"pas d[' ]autre véhicule|aucun autre véhicule|pas de tiers", lowered):
        candidates.append(("third_party_involved", "no", "explicit"))
    elif re.search(r"autre véhicule|autre voiture|véhicule tiers", lowered) or _OTHER_VEHICLE_COLLISION.search(lowered):
        candidates.append(("third_party_involved", "yes", "explicit"))
    if re.search(r"est parti|a pris la fuite|s'est enfui|n'est pas resté", lowered):
        candidates.append(("third_party_presence", "left", "explicit"))
    elif re.search(r"est resté|sur place|est présent", lowered):
        candidates.append(("third_party_presence", "present", "explicit"))
    found = []
    for field, value, uncertainty in candidates:
        fact = VoiceFactV1(field=field, value=value, segment_id=segment.id,
                           excerpt=text, uncertainty=uncertainty)
        try:
            _assert_fact_supported(fact)
        except ValueError:
            continue
        found.append(fact)
    return found


def process_voice_intake(request: VoiceIntakeRequestV1) -> tuple[Intake, VoiceTriageV1]:
    facts = {item.field: item for item in request.facts}
    for item in request.facts:
        _assert_fact_supported(item)
    _choice(facts, "third_party_involved", {"yes", "no", "unknown"})
    _choice(facts, "third_party_presence", {"present", "left", "unknown"})
    danger = _choice(facts, "danger_status", {"yes", "no", "unknown"})
    injury = _choice(facts, "injury_severity", {"none", "minor", "serious", "unknown"})
    _choice(facts, "stationary", {"yes", "no", "unknown"})

    incident_at = None
    time_source = None
    incident_text = _value(facts, "incident_time")
    if incident_text == "just_now":
        segment = next(item for item in request.segments if item.id == facts["incident_time"].segment_id)
        incident_at = segment.observed_at or request.call_started_at
        time_source = "inferred_from_call"
    elif incident_text and incident_text != "unknown":
        if incident_text.startswith("minutes_ago:"):
            segment = next(item for item in request.segments if item.id == facts["incident_time"].segment_id)
            if segment.observed_at is None:
                raise ValueError("Relative incident time requires a timed caller segment")
            incident_at = segment.observed_at - timedelta(minutes=int(incident_text.partition(":")[2]))
            time_source = "inferred_from_call"
        else:
            try:
                parsed = datetime.fromisoformat(incident_text)
            except ValueError as error:
                raise ValueError("incident_time must be an ISO timestamp, relative duration or just_now") from error
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("incident_time must include a timezone")
            incident_at = parsed.astimezone(timezone.utc)
            time_source = "caller_statement"

    missing = [field for field in REQUIRED_ORDER
               if field not in facts or facts[field].uncertainty == "uncertain"
               or (field == "location" and _value(facts, field) == "unknown")
               or (field == "insured_name" and len((_value(facts, field) or "").split()) < 2)]
    # Volunteered information about other vehicles, danger and injuries remains
    # sourced and can trigger an urgent handoff, but is never required on the call.
    unknown_fields = [field for field in ("location",)
                      if _value(facts, field) == "unknown"]
    missing = list(dict.fromkeys([*missing, *unknown_fields]))
    askable = [field for field in missing if field not in unknown_fields]
    reasons = []
    if danger == "yes":
        reasons.append("reported_immediate_danger")
    if injury == "serious":
        reasons.append("reported_serious_injury")
    if reasons:
        triage = VoiceTriageV1(status="urgent_human_handoff", missing_p0=missing,
                               next_question=None, reason_codes=reasons)
    elif request.event_type == "call_failed":
        triage = VoiceTriageV1(status="error", missing_p0=missing,
                               next_question=None, reason_codes=["call_failed"])
    elif request.event_type == "end_of_call_report" and missing:
        triage = VoiceTriageV1(status="incomplete", missing_p0=missing,
                               next_question=None, reason_codes=["call_ended_with_missing_p0"])
    elif askable:
        triage = VoiceTriageV1(status="collecting", missing_p0=missing,
                               next_question=QUESTIONS[askable[0]], reason_codes=[])
    elif missing:
        triage = VoiceTriageV1(status="incomplete", missing_p0=missing,
                               next_question=None, reason_codes=["caller_cannot_confirm_p0"])
    else:
        triage = VoiceTriageV1(status="complete", missing_p0=[],
                               next_question=None, reason_codes=[])

    intake = Intake(
        reported_at=request.call_started_at,
        insured_reference=_value(facts, "insured_reference"),
        insured_name=_value(facts, "insured_name"),
        policy_reference=_value(facts, "policy_reference"),
        incident_at=incident_at,
        time_source=time_source,
        location=(normalize_address(_value(facts, "location") or "") or _value(facts, "location"))
        if facts.get("location") is not None and facts["location"].uncertainty != "uncertain"
        and _value(facts, "location") != "unknown" else None,
        narrative=_value(facts, "narrative") or "",
        danger_status=danger,
        injury_status="yes" if injury in {"minor", "serious"} else "no" if injury == "none" else injury,
        insured_plate=_value(facts, "insured_plate"),
        insured_vehicle=_value(facts, "insured_vehicle"),
    )
    case_missing = missing_intake_fields(intake)
    for field in ("danger_status", "injury_status"):
        if getattr(intake, field) == "unknown" and field not in case_missing:
            case_missing.append(field)
    return intake.model_copy(update={"missing_fields": case_missing}), triage
