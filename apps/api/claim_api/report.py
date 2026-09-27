"""Deterministic, source-aware projection of a persisted case for handler review."""

from __future__ import annotations

from typing import Any

from claim_api.models import AnalysisSourceRef, CaseView, MediaCitation, ReportLine, ReportView


def media_source_ref(citation: MediaCitation) -> AnalysisSourceRef:
    locator = "media:image" if citation.timestamp_seconds is None else f"media:video:{citation.timestamp_seconds}"
    return AnalysisSourceRef(kind="evidence", id=str(citation.evidence_id), locator=locator)


def media_citations(case: CaseView) -> list[MediaCitation]:
    run = case.latest_analysis
    media = run.output.media_analysis if run and run.output else None
    if not media:
        return []
    return [citation for finding in [*media.observations, *media.plates, *media.damages,
            media.liability, *media.liability.vehicle_assessments] for citation in finding.citations]


def analysis_is_current(case: CaseView) -> bool:
    run = case.latest_analysis
    if run is None or run.status != "ready" or run.output is None:
        return False
    if run.input_content_revision == case.content_revision:
        return True
    draft = case.current_draft
    return bool(
        draft and draft.analysis_run_id == run.id
        and draft.content_revision == case.content_revision
    )


def validate_report_refs(case: CaseView, refs: list[AnalysisSourceRef]) -> None:
    evidence = {str(item.id): item for item in case.evidence}
    providers = {str(item.id): item for item in case.provider_results}
    run = case.latest_analysis
    media = run.output.media_analysis if run and run.output else None
    media_refs = set()
    for citation in media_citations(case):
        item = evidence.get(str(citation.evidence_id))
        if (media and citation.evidence_id in media.analyzed_evidence_ids and item
                and item.mime_type.startswith("video/") == (citation.timestamp_seconds is not None)):
            source = media_source_ref(citation)
            media_refs.add((source.id, source.locator))
    for ref in refs:
        if ref.kind == "intake":
            if ref.id != str(case.id) or ref.locator not in type(case.intake).model_fields:
                raise ValueError("Report cites an intake field outside this case.")
            value = getattr(case.intake, ref.locator)
            if value is None or value == "":
                raise ValueError("Report cites an empty intake field.")
        elif ref.kind == "evidence":
            item = evidence.get(ref.id)
            visual_locator = any(
                link.evidence_id == item.id and any(
                    ref.locator == f"video:{obs.get('start_ms')}-{obs.get('end_ms')}"
                    for obs in link.observations)
                for link in case.video_analyses
            ) if item else False
            visual_locator = visual_locator or any(
                result.provider == "vision" and result.status == "matched" and any(
                    fact.get("source_ref") == ref.model_dump(mode="json")
                    for fact in result.data.get("observations", []) if isinstance(fact, dict))
                for result in case.provider_results
            )
            visual_locator = visual_locator or (ref.id, ref.locator) in media_refs
            if item is None or (ref.locator not in {"kind", "role", "source_kind", "mime_type", "byte_size", "received_at", "checksum_status"}
                                and not visual_locator):
                raise ValueError("Report cites evidence outside this case.")
            if not visual_locator and getattr(item, ref.locator) is None:
                raise ValueError("Report cites an empty evidence field.")
        elif ref.kind == "estimate":
            estimate = case.estimate
            if estimate is None or ref.id != str(estimate.id):
                raise ValueError("Report cites an estimate outside this case.")
            if ref.locator.startswith("line_items."):
                if ref.locator.removeprefix("line_items.") not in {line.id for line in estimate.line_items}:
                    raise ValueError("Report cites a missing estimate line.")
            elif ref.locator not in {"total_minor", "currency", "estimate_source", "version"}:
                raise ValueError("Report cites a missing estimate field.")
        elif ref.kind == "voice":
            session = case.voice_session
            if (session is None or ref.id != session.session_id or
                    not ref.locator.startswith("facts.") or
                    not ref.locator.removeprefix("facts.").isdigit() or
                    int(ref.locator.removeprefix("facts.")) >= len(session.facts)):
                raise ValueError("Report cites a voice fact outside this case.")
        else:
            item = providers.get(ref.id)
            if item is None:
                raise ValueError("Report cites a provider result outside this case.")
            if ref.locator in {"status", "mode", "provider", "retrieved_at"}:
                continue
            if ref.locator == "reason" and item.reason:
                continue
            root, dot, key = ref.locator.partition(".")
            values = item.query if root == "query" else item.data if root == "data" else {}
            if not dot or key not in values or values[key] is None:
                raise ValueError("Report cites a missing provider result field.")


def build_report(case: CaseView, edits: list[dict[str, Any]] | None = None) -> ReportView:
    revision = case.content_revision
    lines: list[ReportLine] = []

    def intake_line(key: str, label: str, *, uncertainty: str | None = None) -> None:
        value = getattr(case.intake, key)
        if value is None or value == "":
            return
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        refs = [AnalysisSourceRef(kind="intake", id=str(case.id), locator=key)]
        if key == "incident_at" and case.intake.time_source:
            refs.append(AnalysisSourceRef(kind="intake", id=str(case.id), locator="time_source"))
        lines.append(ReportLine(
            id=f"intake.{key}", text=f"{label} : {text}", claim_kind="declaration",
            source_refs=refs, uncertainty=uncertainty, as_of_revision=revision,
        ))

    intake_line("incident_at", "Heure de l'incident", uncertainty=(
        "Heure déduite de l'appel ; à confirmer." if case.intake.time_source == "inferred_from_call" else None
    ))
    intake_line("location", "Lieu déclaré")
    intake_line("insured_name", "Assurée")
    intake_line("insured_vehicle", "Véhicule assuré")
    intake_line("insured_plate", "Plaque du véhicule assuré")
    intake_line("narrative", "Récit déclaré", uncertainty="Déclaration non corroborée par les pièces.")

    for item in case.evidence:
        name = item.role or item.kind.replace("_", " ")
        lines.append(ReportLine(
            id=f"evidence.{item.id}", text=f"Pièce reçue : {name} ({item.mime_type}).",
            claim_kind="observation", source_refs=[AnalysisSourceRef(
                kind="evidence", id=str(item.id), locator="kind"
            )], uncertainty="La présence de la pièce ne vérifie pas son contenu visuel.",
            as_of_revision=revision, mode=item.mode,
        ))

    for item in case.provider_results:
        lines.append(ReportLine(
            id=f"provider.{item.id}",
            text=f"{item.provider.replace('_', ' ')} : {item.status} ({item.mode}).",
            claim_kind="provider_result",
            source_refs=[AnalysisSourceRef(kind="provider_result", id=str(item.id), locator="status")],
            uncertainty="Résultat simulé ; distinct d'une observation de pièce." if item.mode == "mock" else None,
            as_of_revision=revision, mode=item.mode,
        ))

    run = case.latest_analysis
    current = analysis_is_current(case)
    if run and run.output:
        for index, proposition in enumerate(run.output.propositions):
            kinds = {ref.kind for ref in proposition.source_refs}
            claim_kind = (
                "hypothesis" if proposition.assessment != "supported" or not proposition.source_refs
                else "provider_result" if "provider_result" in kinds
                else "declaration" if kinds == {"intake"}
                else "observation" if kinds == {"evidence"}
                else "hypothesis"
            )
            lines.append(ReportLine(
                id=f"analysis.proposition.{index}", text=proposition.text,
                claim_kind=claim_kind, source_refs=proposition.source_refs,
                uncertainty=proposition.uncertainty_note or (
                    "Analyse obsolète : relancer après modification du dossier." if not current else None
                ), as_of_revision=run.input_content_revision, stale=not current, mode=run.mode,
            ))
        for index, issue in enumerate(run.output.contradictions):
            lines.append(ReportLine(
                id=f"analysis.contradiction.{index}", text=f"Contradiction : {issue.text}",
                claim_kind="hypothesis", source_refs=issue.source_refs,
                uncertainty="Contradiction à arbitrer par le gestionnaire.",
                as_of_revision=run.input_content_revision, stale=not current, mode=run.mode,
            ))
        for index, text in enumerate(run.output.missing_items):
            lines.append(ReportLine(
                id=f"analysis.missing.{index}", text=f"À confirmer : {text}",
                claim_kind="hypothesis", uncertainty="Information absente des sources disponibles.",
                as_of_revision=run.input_content_revision, stale=not current, mode=run.mode,
            ))

    media = run.output.media_analysis if run and run.output else None
    if media:
        def media_line(key, text, citations, *, hypothesis=False, uncertainty=None):
            refs = list({(str(c.evidence_id), c.timestamp_seconds): media_source_ref(c) for c in citations}.values())
            lines.append(ReportLine(
                id=f"analysis.media.{key}", text=text,
                claim_kind="hypothesis" if hypothesis else "observation", source_refs=refs,
                uncertainty=uncertainty or "Analyse visuelle indicative ; à vérifier sur les pièces citées.",
                as_of_revision=run.input_content_revision, stale=not current, mode=run.mode,
            ))
        media_line("summary", media.summary, media_citations(case), hypothesis=True)
        for index, finding in enumerate(media.observations):
            media_line(f"observation.{index}", finding.description, finding.citations)
        for index, finding in enumerate(media.plates):
            media_line(f"plate.{index}", f"{finding.vehicle} — plaque : {finding.plate or 'illisible'}. {finding.description}",
                       finding.citations, uncertainty=f"Lisibilité : {finding.legibility} ; confiance : {finding.confidence}.")
        for index, finding in enumerate(media.damages):
            estimate = finding.estimate
            costing = (f"Fourchette indicative : {estimate.minimum_minor / 100:.2f}–{estimate.maximum_minor / 100:.2f} {estimate.currency}. {estimate.assumptions}"
                       if estimate else "Chiffrage indisponible à partir de ces pièces.")
            media_line(f"damage.{index}", f"{finding.vehicle} — {finding.description} {costing}",
                       finding.citations, hypothesis=True, uncertainty="Estimation visuelle indicative, non montant approuvé.")
        media_line("liability", f"Responsabilité probable : {media.liability.reasoning}",
                   media.liability.citations, hypothesis=True,
                   uncertainty="Validation humaine requise. " + " ".join(media.liability.limitations))
        for index, finding in enumerate(media.liability.vehicle_assessments):
            media_line(f"liability.{index}", f"{finding.vehicle} — {finding.reasoning}",
                       finding.citations, hypothesis=True, uncertainty="Responsabilité probable, à vérifier.")

    if case.scenario_id == "g1":
        if not (current and media and any(p.role == "third_party" and p.legibility == "readable" for p in media.plates)):
            lines.append(ReportLine(
                id="unknown.third_party_plate", text="Plaque de la BMW : inconnue.",
                claim_kind="hypothesis", uncertainty="Aucune plaque tierce corroborée n'est disponible.",
                as_of_revision=revision,
            ))
        if not (current and media and media.damages) and not any(
                ref.kind == "evidence" and ref.locator.startswith("observation.")
                for line in lines for ref in line.source_refs):
            lines.append(ReportLine(
                id="unknown.visual_damage", text="Dégâts visuels : non encore établis.",
                claim_kind="hypothesis", uncertainty="Analyse des pièces requise.",
                as_of_revision=revision,
            ))

    for edit in edits or []:
        old = next((line for line in lines if line.id == edit["line_id"]), None)
        refs = [AnalysisSourceRef.model_validate(ref) for ref in edit["source_refs_json"]]
        correction = ReportLine(
            id=edit["line_id"], text=edit["text"], claim_kind="handler_edit",
            source_refs=refs, uncertainty=edit["uncertainty"],
            as_of_revision=edit["as_of_revision"], stale=edit["as_of_revision"] != revision,
            signed_by=edit["actor_user_id"], signed_at=edit["created_at"],
            previous_text=edit["previous_text"],
        )
        if old and not correction.stale:
            lines[lines.index(old)] = correction
        elif correction.stale:
            correction = correction.model_copy(update={"id": f"stale-edit.{edit['line_id']}"})
            lines.append(correction)
        else:
            lines.append(correction)

    missing_analysis_source = False
    for line in lines:
        # Persisted source references are checked again on read, including older analysis output.
        try:
            validate_report_refs(case, line.source_refs)
        except ValueError:
            line.stale = True
            line.uncertainty = "Source indisponible ou modifiée ; relancer l'analyse."
            if line.id.startswith("analysis."):
                missing_analysis_source = True
    return ReportView(
        lines=lines, analysis_current=current and not missing_analysis_source,
        needs_reanalysis=bool(run and (not current or missing_analysis_source)),
    )
