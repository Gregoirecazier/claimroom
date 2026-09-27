import { useEffect, useRef, useState } from 'react'
import { mediaAnalysisErrors } from '../lib/mediaAnalysis'
import { advanceMediaWorkflow, retryMediaWorkflow, getMediaWorkflow, getCase, editCorrespondence, sendCorrespondence } from '../lib/api'
import type { CaseView, CorrespondenceDraft, MediaWorkflowView } from '../lib/api'

type Props = { caseView: CaseView; accessToken: string; onUpdated: (view: CaseView) => void; pauseUpdates?: boolean }

const statuses: Record<string, string> = {
  queued: 'Analyse en attente', processing: 'Analyse et préparation en cours', waiting: 'Traitement en cours',
  ready: 'Brouillon garage prêt', failed: 'Traitement à relancer', needs_action: 'Intervention requise', stale: 'Résultat ancien',
  draft: 'Brouillon', sending: 'Envoi en cours', sent: 'Accepté par le service email', unknown: 'Envoi à vérifier',
}
const errors: Record<string, string> = {
  dust_not_configured: 'La clé Dust doit être configurée sur le serveur.',
  dust_rate_limited: 'Dust a atteint sa limite de requêtes.', dust_forbidden: 'Dust refuse l’accès à cet agent.',
  dust_submission_interrupted: 'Vérifiez la conversation dans Dust avant de relancer.',
  dust_invalid_report: 'Le rapport Dust ne respecte pas le format attendu.',
  ...mediaAnalysisErrors,
  email_not_configured: 'Le compte Resend et l’expéditeur doivent être configurés.',
  email_draft_stale: 'Le dossier a changé. Préparez un nouveau brouillon.',
  email_reconciliation_required: 'Vérifiez cet envoi dans Resend avant toute nouvelle demande.',
  email_delivery_unknown: 'La réponse du service email est incertaine. Vérifiez son journal.',
}

export function CorrespondenceEditor({ draft, currentRevision, accessToken, caseId, emailConfigured, onSaved }: {
  draft: CorrespondenceDraft; currentRevision: number; accessToken: string; caseId: string;
  emailConfigured: boolean; onSaved: () => void;
}) {
  const [recipient, setRecipient] = useState(draft.recipient)
  const [subject, setSubject] = useState(draft.subject)
  const [body, setBody] = useState(draft.body)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const stale = currentRevision !== draft.content_revision
  const editable = ['draft', 'failed'].includes(draft.status) && !stale
  const changed = recipient !== draft.recipient || subject !== draft.subject || body !== draft.body
  async function act(send: boolean) {
    setBusy(true); setError('')
    try {
      if (send) await sendCorrespondence(accessToken, caseId, draft.id, draft.version)
      else await editCorrespondence(accessToken, caseId, draft.id, { version: draft.version, recipient, subject, body })
      setConfirm(false); onSaved()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Action impossible.') }
    finally { setBusy(false) }
  }
  return <article className="ir-camera-request">
    <strong>{draft.kind === 'garage' ? 'Demande de devis au garage' : 'Demande d’images CCTV'} · {statuses[draft.status] || draft.status}</strong>
    {stale && <p>Le dossier a changé depuis ce brouillon.</p>}
    <label className="ir-field">Destinataire<input type="email" value={recipient} disabled={!editable} onChange={e => setRecipient(e.target.value)} /></label>
    <label className="ir-field">Objet<input value={subject} disabled={!editable} onChange={e => setSubject(e.target.value)} /></label>
    <label className="ir-field">Message<textarea rows={10} value={body} disabled={!editable} onChange={e => setBody(e.target.value)} /></label>
    {draft.source_url && <p><a href={draft.source_url} target="_blank" rel="noreferrer">Source du contact</a></p>}
    {draft.error_code && <p role="alert">{errors[draft.error_code] || draft.error_code}</p>}
    {editable && <button className="ir-button ir-secondary" disabled={busy || !changed} onClick={() => void act(false)}>Enregistrer le brouillon</button>}
    <button className="ir-button ir-secondary" onClick={() => {
      if (!navigator.clipboard) { setError('Sélectionnez le texte pour le copier.'); return }
      void navigator.clipboard.writeText(body).catch(() => setError('Sélectionnez le texte pour le copier.'))
    }}>Copier le message</button>
    {editable && <>
      <label><input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} /> Envoyer cet email au destinataire indiqué</label>
      <button className="ir-button ir-primary" disabled={busy || changed || !confirm || !recipient || !emailConfigured} onClick={() => void act(true)}>Envoyer l’email</button>
    </>}
    {!emailConfigured && editable && <p>L’envoi sera disponible après configuration de Resend.</p>}
    {error && <p role="alert">{error}</p>}
  </article>
}

export function MediaWorkflowPanel({ caseView, accessToken, onUpdated, pauseUpdates = false }: Props) {
  const [view, setView] = useState<MediaWorkflowView | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [retrying, setRetrying] = useState(false)
  const updated = useRef(onUpdated)
  const revision = useRef(caseView.content_revision)
  const fingerprint = useRef('')
  updated.current = onUpdated; revision.current = caseView.content_revision
  async function retry() {
    setRetrying(true); setError('')
    try {
      await retryMediaWorkflow(accessToken, caseView.id, caseView.state_version)
      updated.current(await getCase(accessToken, caseView.id))
      setRefresh(v => v + 1)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Relance impossible.') }
    finally { setRetrying(false) }
  }
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const result = await getMediaWorkflow(accessToken, caseView.id)
        if (!active) return
        setView(result); setError('')
        const next = `${result.content_revision}:${result.workflow?.updated_at || ''}`
        if (!pauseUpdates && (result.content_revision !== revision.current || next !== fingerprint.current)) {
          const current = await getCase(accessToken, caseView.id)
          if (active) updated.current(current)
        }
        if (!pauseUpdates) fingerprint.current = next
        if (['queued', 'waiting', 'processing'].includes(result.workflow?.status || '')) {
          await advanceMediaWorkflow(accessToken, caseView.id)
        }
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : 'Suivi indisponible.') }
      finally { if (active) timer = setTimeout(() => void poll(), 5000) }
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [accessToken, caseView.id, refresh, pauseUpdates])
  return <section className="ir-card" id="garage">
    <div className="ir-section-heading"><h2>Assureurs et demande de devis</h2></div>
    {view?.workflow ? <p role="status">{statuses[view.workflow.status] || view.workflow.status}</p> : <p>Le traitement démarre après le dépôt d’une photo ou vidéo.</p>}
    {view?.workflow?.error_code && <p role="alert">{errors[view.workflow.error_code] || view.workflow.error_code}</p>}
    {view?.workflow?.status === 'failed' && <button className="ir-button ir-secondary" disabled={retrying || pauseUpdates} onClick={() => void retry()}>
      {retrying ? 'Relance en cours…' : 'Relancer le traitement'}
    </button>}
    {!!view?.workflow?.insurance_matches.length && <>
      <p>Correspondances dans la base de démonstration · 1 000 plaques FR et 1 000 UK. Associations fictives, à distinguer d’une vérification réelle.</p>
      <table><thead><tr><th>Véhicule / plaque observée</th><th>Assureur dans la base</th><th>Situation à la date du sinistre</th></tr></thead><tbody>
        {view.workflow.insurance_matches.map((item, i) => <tr key={i}><td>{item.vehicle} · {item.plate || 'Illisible'}</td>
          <td>{item.data.insurer_name || 'Aucun assureur identifié'}</td>
          <td>{item.status === 'matched' ? 'Contrat actif dans la base fictive' : item.reason || item.status}</td></tr>)}
      </tbody></table>
    </>}
    {view?.correspondence.filter(d => d.kind === 'garage').map(draft => <CorrespondenceEditor key={`${draft.id}:${draft.version}:${draft.status}`}
      draft={draft} currentRevision={view.content_revision} caseId={caseView.id} accessToken={accessToken}
      emailConfigured={view.email_configured} onSaved={() => setRefresh(v => v + 1)} />)}
    {error && <p role="alert">{error}</p>}
  </section>
}
