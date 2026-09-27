"""Deterministic, source-bounded follow-up message composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone

from claim_api.models import Intake


@dataclass(frozen=True)
class SmsContent:
    body: str
    missing_items: tuple[str, ...]
    source_refs: tuple[str, ...]
    warnings: tuple[str, ...]


def _summary(narrative: str) -> tuple[str, bool]:
    clean = " ".join(narrative.split())
    if len(clean) <= 220:
        return clean, False
    cutoff = clean.rfind(" ", 0, 218)
    if cutoff < 100:
        cutoff = 217
    return clean[:cutoff].rstrip(" ,;:") + "…", True


def compose_follow_up(
    intake: Intake,
    *,
    evidence_kinds: set[str],
    facts: list[dict],
    deposit_url: str,
    synthetic_demo: bool = False,
) -> SmsContent:
    """Use only current declared facts and finalised evidence, never model guesses."""
    if not deposit_url.startswith("https://") or "#token=" not in deposit_url:
        raise ValueError("A secure deposit URL is required")

    narrative, shortened = _summary(intake.narrative)
    lines = ["Claimroom insurance", f"Suite à votre déclaration : {narrative}"]
    source_refs = ["case:intake:narrative"]
    warnings = ["summary_shortened"] if shortened else []

    if intake.incident_at is not None:
        local = intake.incident_at.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        if intake.time_source == "inferred_from_call":
            lines.append(f"Heure estimée à partir de l'appel : {local}.")
        elif intake.time_source == "caller_statement":
            lines.append(f"Heure déclarée : {local}.")
        elif intake.time_source == "handler_entered":
            lines.append(f"Heure renseignée dans le dossier : {local}.")
        source_refs.append("case:intake:incident_at")

    missing: list[str] = []
    if not intake.insured_reference:
        missing.append("référence de sinistre, si disponible")
    if not intake.location:
        missing.append("lieu de l'accident")
    if not intake.incident_at:
        missing.append("date et heure de l'accident")
    if not intake.injury_status or intake.injury_status == "unknown":
        missing.append("présence éventuelle de blessés")
    if not intake.danger_status or intake.danger_status == "unknown":
        missing.append("danger actuel éventuel")
    if not intake.insured_plate:
        missing.append("immatriculation de votre véhicule")
    if not intake.insured_vehicle:
        missing.append("modèle de votre véhicule")
    if not intake.policy_reference:
        missing.append("référence de votre contrat, si disponible")
    if missing:
        lines.append("À compléter : " + ", ".join(missing) + ".")

    instructions: list[str] = []
    if not evidence_kinds.intersection({"scene_photo", "scene_video"}):
        instructions.append("une vue large du lieu et des véhicules")
    if "damage_photo" not in evidence_kinds:
        instructions.append("un gros plan des dégâts")
    third_party = next((fact.get("value") for fact in facts if fact.get("field") == "third_party_involved"), None)
    if third_party == "yes" and "scene_video" not in evidence_kinds:
        instructions.append("si disponible, une photo ou vidéo du tiers et de sa plaque")
    if instructions:
        lines.append("Pièces utiles : " + ", ".join(instructions[:3]) + ".")
    if synthetic_demo and "scene_video" not in evidence_kinds:
        lines.append("Une vidéo de démonstration peut être choisie depuis votre dossier.")

    lines.append("Accès sécurisé à votre dossier : " + deposit_url)
    return SmsContent("\n".join(lines), tuple(missing), tuple(source_refs), tuple(warnings))
