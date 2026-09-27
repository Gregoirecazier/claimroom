import { formatMoney as money } from '../lib/money'
import type { CaseView, Evidence, MediaCitation } from '../lib/api'
import { currentMediaAnalysis, mediaAnalysisStatus } from '../lib/mediaAnalysis'

type Props = {
  caseView: CaseView
  busy: boolean
  onAnalyze: () => void
  onOpenEvidence: (evidence: Evidence) => void
}
const confidence: Record<string, string> = { low: 'Faible', medium: 'Moyenne', high: 'Élevée' }
const roles: Record<string, string> = { insured: 'Assuré', third_party: 'Tiers', unknown: 'Rôle à confirmer' }
const responsibility: Record<string, string> = {
  insured: 'Assuré', third_party: 'Tiers', shared: 'Partagée', undetermined: 'Indéterminée',
  likely_responsible: 'Responsabilité probable', possibly_contributing: 'Contribution possible',
  no_visible_contribution: 'Aucune contribution visible',
}
const legibility: Record<string, string> = { readable: 'Lisible', partial: 'Partiellement lisible', unreadable: 'Illisible' }

export function VisionPanel({ caseView, busy, onAnalyze, onOpenEvidence }: Props) {
  const media = caseView.evidence.filter(item => item.mime_type.startsWith('image/') || item.mime_type.startsWith('video/'))
  const result = currentMediaAnalysis(caseView)
  const running = busy || (caseView.latest_analysis?.status === 'running'
    && caseView.latest_analysis.input_content_revision === caseView.content_revision)
  const citations = (refs: MediaCitation[]) => refs.map((ref, index) => {
    const evidence = caseView.evidence.find(item => item.id === ref.evidence_id)
    return evidence && <button key={`${ref.evidence_id}-${index}`} className="ir-text-button" onClick={() => onOpenEvidence(evidence)}>
      {evidence.original_filename || 'Voir la pièce'}{ref.timestamp_seconds !== null ? ` · ${ref.timestamp_seconds} s` : ''}
    </button>
  })
  return <section className="ir-card" id="vision">
    <div className="ir-section-heading"><h2>Analyse des photos et vidéos <span className="ir-inline-count">{media.length}</span></h2>
      <button className="ir-button ir-primary" disabled={running || !media.length} onClick={onAnalyze}>{running ? 'Analyse en cours…' : result ? 'Actualiser l’analyse' : 'Analyser les pièces'}</button>
    </div>
    <p>Chaque photo ou vidéo reçue dans le dossier déclenche automatiquement une analyse de l’ensemble des pièces.</p>
    <p role="status">{mediaAnalysisStatus(caseView)}</p>
    {media.length === 0 && <p>Aucun média visuel joint.</p>}
    {media.map(item => <div className="ir-video-row" key={item.id}>
      <span className="ir-video-icon">{item.mime_type.startsWith('video/') ? '▶' : '▧'}</span>
      <div><strong>{item.original_filename || item.role || item.kind}</strong><p>{result?.analyzed_evidence_ids.includes(item.id) ? 'Analysée par Gemini' : running ? 'Analyse en cours…' : 'Pièce à analyser'}</p></div>
      <button className="ir-button ir-secondary" onClick={() => onOpenEvidence(item)}>Voir</button>
    </div>)}
    {result && <div className="ir-media-findings">
      <h3>Déroulé de l’accident</h3>
      <p>{result.summary}</p>
      {result.observations.map((item, index) => <p key={`observation-${index}`}>{item.description} <small>Confiance : {confidence[item.confidence]}</small> {citations(item.citations)}</p>)}
      <h3>Véhicules et plaques</h3>
      {!result.plates.length && <p>Aucune plaque établie dans cette analyse.</p>}
      {result.plates.map((item, index) => <div key={`plate-${index}`}><p><strong>{item.vehicle} · {item.plate || 'Plaque illisible'}</strong> — {roles[item.role]} · {legibility[item.legibility]} · Confiance : {confidence[item.confidence]}</p><p>{item.description} {citations(item.citations)}</p></div>)}
      <h3>Responsabilité probable · à vérifier</h3>
      <p><strong>{responsibility[result.liability.likely_responsible]}</strong> — {result.liability.reasoning} <small>Confiance : {confidence[result.liability.confidence]}</small> {citations(result.liability.citations)}</p>
      {result.liability.vehicle_assessments?.map((item, index) => <div key={`liability-${index}`}><p><strong>{item.vehicle} · {responsibility[item.assessment]}</strong> — {roles[item.role]}</p><p>{item.reasoning} <small>Confiance : {confidence[item.confidence]}</small> {citations(item.citations)}</p></div>)}
      <h3>Dégâts et estimation par véhicule</h3>
      {!result.damages.length && <p>Dégâts non établis ; estimation indisponible.</p>}
      {result.damages.map((item, index) => <div key={`damage-${index}`}><p><strong>{item.vehicle}{item.affected_parts.length > 0 && ` · ${item.affected_parts.join(', ')}`}</strong> — {item.description} {citations(item.citations)}</p>
        {item.estimate ? <p>Estimation indicative : {money(item.estimate.minimum_minor, item.estimate.currency)}–{money(item.estimate.maximum_minor, item.estimate.currency)}. {item.estimate.assumptions}</p>
          : <p>Chiffrage impossible à partir de ces pièces ; devis ou vues complémentaires nécessaires.</p>}
      </div>)}
      <p>{result.cross_evidence_consistency}</p>
      {[...result.limitations, ...result.liability.limitations].map((item, index) => <p key={`limit-${index}`}>{item}</p>)}
    </div>}
  </section>
}
