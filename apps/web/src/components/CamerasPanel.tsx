import { useEffect, useState } from 'react'
import {
  approveCameraRequest, createCameraRequest, receiveCctv,
  searchCameras, updateCameraRequestStatus,
} from '../lib/api'
import type { CameraSearch, CaseView } from '../lib/api'
import { CameraMap } from './CameraMap'

type Props = {
  caseView: CaseView
  accessToken: string
  onUpdated: (caseView: CaseView) => void
}

const formatDate = (value: string | null) => value
  ? new Intl.DateTimeFormat('fr-FR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
  : 'Créneau à vérifier'

const statusLabel: Record<string, string> = {
  draft: 'Brouillon', requested: 'Approuvée localement · aucun envoi',
  waiting: 'En attente simulée', denied: 'Refus simulé',
  unknown_owner: 'Responsable inconnu', received: 'Pièce synthétique reçue',
  unavailable: 'Indisponible', timed_out: 'Délai dépassé',
}

export function CamerasPanel({ caseView, accessToken, onUpdated }: Props) {
  const [search, setSearch] = useState<CameraSearch | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [scope, setScope] = useState('Images du lieu déclaré, cinq minutes avant et après l’accident.')
  const [reason, setReason] = useState('Vérifier les circonstances de l’accident déclaré.')

  useEffect(() => {
    let active = true
    setSearch(null)
    setLoading(true)
    void searchCameras(accessToken, caseView.id)
      .then(result => { if (active) setSearch(result) })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Recherche indisponible.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [accessToken, caseView.id, caseView.intake.location, caseView.intake.incident_at])

  async function change(run: () => Promise<CaseView>) {
    setBusy(true); setError('')
    try { onUpdated(await run()) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Action CCTV indisponible.') }
    finally { setBusy(false) }
  }

  const candidate = search?.candidates[0]
  const request = caseView.camera_requests?.[0]
  const legacyReceived = !request && caseView.timeline.some(event => event.event_type === 'case.cctv_received')
  const terminal = request && ['denied', 'unknown_owner', 'unavailable', 'timed_out'].includes(request.status)

  return <section className="ir-card ir-camera-panel" id="cameras">
    <div className="ir-section-heading"><span className="ir-heading-icon">▣</span><div><p className="ir-eyebrow">BRANCHE OPTIONNELLE · S13</p><h2>Caméras proches</h2></div><span className="ir-source-tag">Camérci + simulation</span></div>
    <CameraMap caseId={caseView.id} address={caseView.intake.location} accessToken={accessToken} />
    {loading && <p>Recherche des caméras de démonstration…</p>}
    {candidate && <div className="ir-camera-candidate"><strong>{candidate.label}</strong><p>Source : fixture {search?.source_version} · {candidate.controller || 'responsable inconnu'}</p><p>Créneau probable : {formatDate(candidate.likely_from)} – {formatDate(candidate.likely_to)}. Présence et disponibilité non vérifiées.</p></div>}
    {legacyReceived && <p>Une image CCTV synthétique a déjà été ajoutée à ce dossier avant le suivi des demandes.</p>}
    {candidate && !request && !legacyReceived && <div className="ir-camera-draft">
      <label className="ir-field">Périmètre demandé<textarea rows={2} value={scope} onChange={event => setScope(event.target.value)} /></label>
      <label className="ir-field">Motif<textarea rows={2} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <p>Destinataire fictif : {candidate.recipient || 'inconnu'}</p>
      <button className="ir-button ir-secondary" disabled={busy || !candidate.recipient || !candidate.controller} onClick={() => void change(() => createCameraRequest(accessToken, caseView.id, candidate.id, scope, reason, caseView.state_version))}>Préparer la demande simulée</button>
    </div>}
    {request && <div className="ir-camera-request">
      <strong>{request.candidate_label} · {statusLabel[request.status]}</strong>
      <p>{request.scope} Motif : {request.reason}</p>
      <p>Responsable : {request.controller || 'inconnu'} · Destinataire : {request.recipient || 'inconnu'}</p>
      {request.approved_at && <p>Approuvée le {formatDate(request.approved_at)}. Aucune transmission externe.</p>}
      {request.status === 'draft' && <button className="ir-button ir-secondary" disabled={busy || !candidate} onClick={() => void change(() => approveCameraRequest(accessToken, caseView.id, request.id, caseView.state_version))}>Approuver localement</button>}
      {request.status === 'requested' && <button className="ir-button ir-secondary" disabled={busy} onClick={() => void change(() => updateCameraRequestStatus(accessToken, caseView.id, request.id, 'waiting', caseView.state_version))}>Marquer en attente</button>}
      {['requested', 'waiting'].includes(request.status) && <>
        <button className="ir-button ir-secondary" disabled={busy} onClick={() => void change(() => updateCameraRequestStatus(accessToken, caseView.id, request.id, 'denied', caseView.state_version))}>Noter un refus simulé</button>
        <button className="ir-button ir-secondary" disabled={busy} onClick={() => void change(() => updateCameraRequestStatus(accessToken, caseView.id, request.id, 'timed_out', caseView.state_version))}>Noter un délai dépassé</button>
        {request.fixture_event_id && <button className="ir-button ir-secondary" disabled={busy} onClick={() => void change(() => receiveCctv(accessToken, caseView.id, request.fixture_event_id!, caseView.state_version, request.id))}>Ajouter la réception démo</button>}
      </>}
      {terminal && <p>Absence de pièce CCTV : l’analyse continue avec les preuves existantes.</p>}
      {request.status === 'received' && <p>Image synthétique liée au dossier. Voir les pièces ci-dessous.</p>}
    </div>}
    {error && <p className="ir-error" role="alert">{error}</p>}
  </section>
}
