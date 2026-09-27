import { useState } from 'react'
import { editReportLine } from '../lib/api'
import type { AnalysisSourceRef, CaseView, Evidence, ReportLine } from '../lib/api'

type Props = {
  caseView: CaseView
  accessToken: string
  onUpdated: (caseView: CaseView) => void
  onOpenEvidence: (evidence: Evidence) => void
}

const kindLabels: Record<ReportLine['claim_kind'], string> = {
  declaration: 'Déclaration', observation: 'Constat de pièce',
  provider_result: 'Résultat fournisseur', hypothesis: 'À vérifier',
  handler_edit: 'Modification signée',
}

const redundantNotes = new Set([
  'Déclaration non corroborée par les pièces.',
  "Résultat simulé ; distinct d'une observation de pièce.",
])

export function formatReportText(text: string): string {
  return text.replace(/\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b/g, value => {
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('fr-FR', {
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).format(date)
  })
}

export function sourceDetail(caseView: CaseView, ref: AnalysisSourceRef): string | null {
  if (ref.kind === 'intake') {
    if (ref.id !== caseView.id || !(ref.locator in caseView.intake)) return null
    const value = caseView.intake[ref.locator as keyof CaseView['intake']]
    return value === null || value === undefined ? null : `${ref.locator} : ${String(value)}`
  }
  if (ref.kind === 'evidence') {
    const item = caseView.evidence.find(evidence => evidence.id === ref.id)
    return item ? `${item.original_filename || item.role || item.kind} · ${item.mime_type} · SHA-256 ${item.client_sha256 || 'indisponible'} (${item.checksum_status})` : null
  }
  const provider = caseView.provider_results.find(item => item.id === ref.id)
  if (!provider) return null
  const [root, key] = ref.locator.split('.', 2)
  const value = root === 'query' ? provider.query[key] : root === 'data' ? provider.data[key]
    : ref.locator === 'reason' ? provider.reason : provider[ref.locator as keyof typeof provider]
  return value === null || value === undefined ? null
    : `${provider.provider} · ${provider.mode} · ${ref.locator} : ${String(value)}`
}

export function CaseReport({ caseView, accessToken, onUpdated, onOpenEvidence }: Props) {
  const [editing, setEditing] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [uncertainty, setUncertainty] = useState('')
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function showSource(ref: AnalysisSourceRef) {
    const detail = sourceDetail(caseView, ref)
    setSource(detail || 'Source disparue ou indisponible ; relancez l’analyse.')
    if (ref.kind === 'evidence') {
      const evidence = caseView.evidence.find(item => item.id === ref.id)
      if (evidence) onOpenEvidence(evidence)
    }
  }

  function startEdit(line: ReportLine) {
    setEditing(line.id); setText(line.text); setUncertainty(line.uncertainty || ''); setError('')
  }

  async function save(lineId: string) {
    setBusy(true); setError('')
    try {
      const updated = await editReportLine(accessToken, caseView.id, lineId, text, uncertainty.trim() || null, caseView.state_version)
      onUpdated(updated); setEditing(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Correction impossible.')
    } finally { setBusy(false) }
  }

  return <section className="ir-card ir-report" id="sourced-report" aria-labelledby="sourced-report-title">
    <div className="ir-section-heading"><span className="ir-heading-icon">≡</span><div><h2 id="sourced-report-title">Compte rendu de l’accident</h2></div></div>
    {caseView.report.needs_reanalysis && <p className="ir-estimate-note" role="status">L’analyse précédente est obsolète après modification du dossier. Relancez l’analyse avant toute validation.</p>}
    {!caseView.latest_analysis && <p className="ir-estimate-note">Aucune analyse lancée. Les pièces listées ci-dessous sont reçues, mais leur contenu visuel n’est pas encore analysé.</p>}
    <ol className="ir-report-lines">{caseView.report.lines.map(line => <li key={line.id} className={line.stale ? 'ir-report-stale' : ''}>
      <div className="ir-report-line-heading"><span className="ir-source-tag">{kindLabels[line.claim_kind]}{line.mode === 'mock' ? ' · simulation' : ''}</span>{line.stale && <small>À actualiser</small>}</div>
      <p>{formatReportText(line.text)}</p>
      {line.uncertainty && !redundantNotes.has(line.uncertainty) && <small className="ir-report-uncertainty">{line.uncertainty}</small>}
      {line.signed_by && <small>Modifié par {line.signed_by} le {line.signed_at ? new Date(line.signed_at).toLocaleString('fr-FR') : 'date inconnue'}</small>}
      <div className="ir-report-sources">{line.source_refs.filter(ref => ref.kind === 'evidence').map((ref, index) => <button key={`${ref.kind}-${ref.id}-${ref.locator}-${index}`} className="ir-text-button" onClick={() => showSource(ref)}>Ouvrir la pièce</button>)}
        {!line.stale && line.source_refs.length > 0 && <button className="ir-text-button" onClick={() => startEdit(line)}>Modifier</button>}
      </div>
      {editing === line.id && <div className="ir-report-edit"><label className="ir-field">Texte<textarea rows={3} value={text} onChange={event => setText(event.target.value)} /></label><label className="ir-field">Incertitude<textarea rows={2} value={uncertainty} onChange={event => setUncertainty(event.target.value)} /></label><button className="ir-button ir-primary" disabled={busy || !text.trim()} onClick={() => void save(line.id)}>Enregistrer la modification</button><button className="ir-button ir-secondary" onClick={() => setEditing(null)}>Annuler</button></div>}
    </li>)}</ol>
    {source && <div className="ir-estimate-note" role="status">{source}<button className="ir-text-button" onClick={() => setSource('')}>Fermer</button></div>}
    {error && <p className="ir-error" role="alert">{error}</p>}
  </section>
}
