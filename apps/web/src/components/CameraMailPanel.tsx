import { useEffect, useState } from 'react'
import { createCameraMail, getMediaWorkflow } from '../lib/api'
import type { CaseView, MediaWorkflowView } from '../lib/api'
import { CorrespondenceEditor } from './MediaWorkflowPanel'

export function CameraMailPanel({ caseView, accessToken }: { caseView: CaseView; accessToken: string }) {
  const [camera, setCamera] = useState('')
  const [controller, setController] = useState('')
  const [recipient, setRecipient] = useState('')
  const [source, setSource] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [view, setView] = useState<MediaWorkflowView | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    getMediaWorkflow(accessToken, caseView.id).then(result => { if (active) setView(result) })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Suivi indisponible.') })
    return () => { active = false }
  }, [accessToken, caseView.id, caseView.content_revision, refresh])
  async function prepare() {
    setBusy(true); setError('')
    try {
      await createCameraMail(accessToken, caseView.id, { expected_state_version: caseView.state_version,
        camera_label: camera, controller, recipient, source_url: source, recipient_confirmed: confirmed })
      setRefresh(v => v + 1)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Demande indisponible.') }
    finally { setBusy(false) }
  }
  return <section className="ir-card">
    <div className="ir-section-heading"><h2>Contacter le responsable d’une caméra</h2></div>
    <p>Renseignez le contact identifié pour la caméra repérée. Vous pourrez vérifier le message avant son envoi réel.</p>
    <form onSubmit={e => { e.preventDefault(); void prepare() }}>
      <label className="ir-field">Caméra / emplacement<input required minLength={3} value={camera} onChange={e => setCamera(e.target.value)} /></label>
      <label className="ir-field">Organisme responsable<input required minLength={3} value={controller} onChange={e => setController(e.target.value)} /></label>
      <label className="ir-field">Email du responsable<input type="email" required value={recipient} onChange={e => setRecipient(e.target.value)} /></label>
      <label className="ir-field">Page indiquant ce contact<input type="url" required value={source} onChange={e => setSource(e.target.value)} /></label>
      <label><input type="checkbox" required checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> J’ai vérifié que ce contact correspond au responsable de la caméra.</label>
      <button className="ir-button ir-primary" disabled={busy || !confirmed}>Préparer la demande</button>
    </form>
    {view?.correspondence.filter(d => d.kind === 'cctv').map(draft => <CorrespondenceEditor key={`${draft.id}:${draft.version}:${draft.status}`}
      draft={draft} currentRevision={caseView.content_revision} caseId={caseView.id} accessToken={accessToken}
      emailConfigured={view.email_configured} onSaved={() => setRefresh(v => v + 1)} />)}
    {error && <p role="alert">{error}</p>}
  </section>
}
