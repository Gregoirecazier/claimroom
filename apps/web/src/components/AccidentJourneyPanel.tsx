import { formatMoney as money } from '../lib/money'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { accidentVideo, advanceMediaWorkflow, approveAccident, editAccidentEstimate, getAccidentJourney, getCase, getMediaWorkflow, retryMediaWorkflow, sendAccidentNotifications } from '../lib/api'
import type { AccidentJourneyView, CaseView, Evidence, MediaWorkflowView } from '../lib/api'
import { blockerLabel, concisePoints, involvedVehicles, vehicleName } from '../lib/analysisPresentation'
import { RepairCostBreakdown } from './RepairCostBreakdown'
import './accident-journey.css'

const statusLabels: Record<string, string> = { queued: 'En attente', processing: 'Analyse en cours…', waiting: 'Nouvelle tentative programmée', ready: 'Analyse terminée', failed: 'Analyse interrompue', needs_action: 'Reprise nécessaire' }
const errorLabels: Record<string, string> = { astra_not_configured: 'Configurez la clé OpenAI sur le serveur.', astra_rate_limited: 'La limite OpenAI est atteinte. Le traitement sera réessayé.', astra_unavailable: 'OpenAI est temporairement indisponible.', astra_request_rejected: 'OpenAI a refusé la requête. Vérifiez l’accès à Astra.', photos_required: 'Ajoutez une vue du véhicule et des photos rapprochées des dommages.', urgent_human_handoff: 'La déclaration nécessite une reprise humaine prioritaire.', workflow_failed: 'Le traitement a échoué. Vous pouvez le relancer.' }

const responsibilityLabels: Record<string, string> = {
  likely_responsible: 'Responsabilité probable', no_visible_contribution: 'Aucune contribution visible',
  undetermined: 'À déterminer', possibly_contributing: 'Contribution possible',
}
const confidenceLabels: Record<string, string> = { high: 'Élevée', medium: 'Modérée', low: 'Faible' }
const severityLabels: Record<string, string> = { minor: 'Léger', moderate: 'Modéré', severe: 'Important', unknown: 'À préciser' }
const deliveryStatus = (status: string, mode: 'simulated' | 'live') => {
  if (status === 'sent') return mode === 'simulated' ? 'Simulé' : 'Envoyé'
  return { simulated: 'Simulé', queued: 'En attente', sending: 'Envoi en cours', failed: 'Échec', unknown: 'Envoi à vérifier', cancelled: 'Annulé · dossier modifié' }[status] || 'État à vérifier'
}
type CostRange = { minimum_minor: number; maximum_minor: number }
const toEuros = (minor: number) => String(minor / 100)
const toMinor = (euros: string) => Math.round(Number(euros.replace(',', '.')) * 100)

function BulletPoints({ items }: { items: string[] }) {
  return items.length ? <ul className="aj-facts">{items.map((item, i) => <li key={i}>{item}</li>)}</ul> : null
}

export function AccidentJourneyPanel({ caseView, accessToken, onUpdated, onOpenEvidence, garageHelp, hidden = false, pauseUpdates = false }: {
  caseView: CaseView; accessToken: string; onUpdated: (view: CaseView) => void; onOpenEvidence: (e: Evidence) => void; garageHelp?: ReactNode; hidden?: boolean; pauseUpdates?: boolean
}) {
  const [view, setView] = useState<AccidentJourneyView | null>(null)
  const [workflow, setWorkflow] = useState<MediaWorkflowView | null>(null)
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [editingEstimate, setEditingEstimate] = useState(false)
  const [costRanges, setCostRanges] = useState<CostRange[]>([])
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const [busy, setBusy] = useState(false)
  const [video, setVideo] = useState<string | null>(null)
  const [videoStart, setVideoStart] = useState<number | null>(null)
  const updated = useRef(onUpdated); updated.current = onUpdated
  const pause = useRef(pauseUpdates); pause.current = pauseUpdates
  const activeReview = useRef('')
  useEffect(() => {
    let active = true; let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const [journey, progress] = await Promise.all([getAccidentJourney(accessToken, caseView.id), getMediaWorkflow(accessToken, caseView.id)])
        if (!active) return
        setView(journey); setWorkflow(progress); setPollError('')
        if (journey.review?.id !== activeReview.current) {
          activeReview.current = journey.review?.id || ''
          const estimate = journey.review?.assessment.repair_estimate
          setAmount(estimate ? String(Math.floor((estimate.minimum_minor + estimate.maximum_minor) / 2) / 100) : '')
          setCostRanges(estimate ? (estimate.line_items?.length ? estimate.line_items : [estimate]).map(item => ({
            minimum_minor: item.minimum_minor, maximum_minor: item.maximum_minor,
          })) : [])
          setEditingEstimate(false)
          setReason(''); setVideo(null); setVideoStart(null)
        }
        if (!pause.current) { const current = await getCase(accessToken, caseView.id); if (active && !pause.current) updated.current(current) }
        if (['queued','processing','waiting'].includes(progress.workflow?.status || '') || journey.notifications.some(n => ['queued','sending'].includes(n.status))) {
          await advanceMediaWorkflow(accessToken, caseView.id)
        }
      } catch (e) { if (active) setPollError(e instanceof Error ? e.message : 'Suivi indisponible.') }
      finally { if (active) timer = setTimeout(() => void poll(), 5000) }
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [accessToken, caseView.id, caseView.content_revision])
  useEffect(() => () => { if (video) URL.revokeObjectURL(video) }, [video])
  const review = view?.review; const assessment = review?.assessment; const estimate = assessment?.repair_estimate
  const proposed = estimate ? Math.floor((estimate.minimum_minor + estimate.maximum_minor) / 2) : null
  const selectedAmount = toMinor(amount)
  const amended = proposed !== selectedAmount
  const costItems = estimate?.line_items?.length ? estimate.line_items : estimate ? [estimate] : []
  const costTotal = costRanges.reduce((total, item) => ({
    minimum_minor: total.minimum_minor + item.minimum_minor,
    maximum_minor: total.maximum_minor + item.maximum_minor,
  }), { minimum_minor: 0, maximum_minor: 0 })
  const validCosts = costRanges.length === costItems.length && costRanges.every(item =>
    Number.isInteger(item.minimum_minor) && Number.isInteger(item.maximum_minor) &&
    item.minimum_minor >= 0 && item.maximum_minor >= item.minimum_minor && item.maximum_minor <= 100_000_000) &&
    costTotal.maximum_minor > 0
  const costsChanged = costRanges.some((item, index) => item.minimum_minor !== costItems[index]?.minimum_minor ||
    item.maximum_minor !== costItems[index]?.maximum_minor)
  function updateCost(index: number, bound: keyof CostRange, value: number) {
    setCostRanges(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, [bound]: value } : item))
  }
  async function saveEstimate() {
    if (!view || !review || !validCosts || !costsChanged) return
    setBusy(true); setError('')
    try {
      const result = await editAccidentEstimate(accessToken, caseView.id, {
        review_id: review.id, expected_content_revision: view.content_revision,
        ...costTotal, line_items: estimate?.line_items?.length ? costRanges : [],
      })
      setView(result)
      setAmount(toEuros(Math.floor((costTotal.minimum_minor + costTotal.maximum_minor) / 2)))
      setReason('')
      setEditingEstimate(false)
      updated.current(await getCase(accessToken, caseView.id))
    } catch (e) { setError(e instanceof Error ? e.message : 'Modification du chiffrage impossible.') }
    finally { setBusy(false) }
  }
  async function approve() {
    if (!view || !review) return
    setBusy(true); setError('')
    try {
      setView(await approveAccident(accessToken, caseView.id, { review_id: review.id, expected_content_revision: view.content_revision, amount_minor: selectedAmount, amendment_reason: reason }))
      updated.current(await getCase(accessToken, caseView.id))
    } catch (e) { setError(e instanceof Error ? e.message : 'Validation impossible.') }
    finally { setBusy(false) }
  }
  async function sendNotifications() {
    if (!view || !review) return
    setBusy(true); setError('')
    try {
      setView(await sendAccidentNotifications(accessToken, caseView.id, {
        review_id: review.id, expected_content_revision: view.content_revision, confirm_send: true,
      }))
      updated.current(await getCase(accessToken, caseView.id))
    } catch (e) { setError(e instanceof Error ? e.message : 'Envoi impossible.') }
    finally { setBusy(false) }
  }
  async function retry() {
    setBusy(true); setError('')
    try { await retryMediaWorkflow(accessToken, caseView.id, caseView.state_version); updated.current(await getCase(accessToken, caseView.id)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Relance impossible.') }
    finally { setBusy(false) }
  }
  async function showVideo(seconds: number | null = null) {
    if (!assessment?.selected_video_id) return
    try { setVideoStart(seconds); setVideo(await accidentVideo(accessToken, caseView.id, assessment.selected_video_id)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Vidéo indisponible.') }
  }
  function cite(id: string, seconds: number | null) {
    const evidence = caseView.evidence.find(e => e.id === id)
    return evidence?.mime_type.startsWith('video/') && assessment?.selected_video_id
      ? <button className="ir-text-button" onClick={() => void showVideo(seconds)}>Voir la vidéo{seconds != null ? ` · ${seconds.toFixed(1)} s` : ''}</button>
      : evidence ? <button className="ir-text-button" onClick={() => onOpenEvidence(evidence)}>{evidence.original_filename || 'Voir la pièce'}</button>
      : assessment?.selected_video_id ? <button className="ir-text-button" onClick={() => void showVideo(seconds)}>Voir la vidéo{seconds != null ? ` · ${seconds.toFixed(1)} s` : ''}</button>
      : <span>Source vidéo indisponible{seconds != null ? ` · ${seconds.toFixed(1)} s` : ''}</span>
  }
  const blockers = [...new Set([...(review?.blockers || []), ...(assessment?.missing_information || [])].map(blockerLabel))]
  const vehicles = assessment ? involvedVehicles(assessment) : []
  const vehicleLabels = new Set(vehicles.map(plate => plate.vehicle))
  const damages = assessment?.media.damages.filter(damage => vehicleLabels.has(damage.vehicle)) || []
  const facts = assessment ? assessment.key_facts?.length ? assessment.key_facts : concisePoints(assessment.media.summary) : []
  const responsible = vehicles.find(plate => plate.vehicle === assessment?.at_fault_vehicle)
  const workflowStatus = workflow?.workflow?.status || ''
  const status = review ? review.status === 'approved' ? 'Validé' : review.status === 'awaiting_review' ? 'À valider' : 'À compléter'
    : statusLabels[workflowStatus] || 'En attente des photos'
  return <div className="cr-section-stack aj-journey" hidden={hidden} id="automatic-journey">
    <section className="ir-card aj-header" id="analysis" aria-labelledby="analysis-title">
      <div className="aj-heading"><h2 id="analysis-title">Analyse et décision</h2><span className={`aj-status ${review?.status === 'needs_information' || workflowStatus === 'failed' ? 'aj-warning' : ''}`} role="status"><i />{status}</span></div>
      {workflow?.workflow?.error_code && <p className="ir-error" role="alert">{errorLabels[workflow.workflow.error_code] || workflow.workflow.error_code}</p>}
      {workflowStatus === 'failed' && <button className="ir-button ir-secondary" disabled={busy} onClick={() => void retry()}>Relancer l’analyse</button>}
      {blockers.length > 0 && <div className="aj-blockers"><h3>À compléter</h3><ul>{blockers.map((item, i) => <li key={i}>{item}</li>)}</ul></div>}
    </section>
    {assessment && <div className="aj-grid">
      <div className="aj-main">
        <section className="ir-card aj-accident" aria-labelledby="accident-title">
          <div className="aj-heading"><h2 id="accident-title">L’accident</h2></div>
          <BulletPoints items={facts} />
          <div className="aj-conclusion"><div><span className="aj-label">Responsabilité probable</span><strong>{responsible ? vehicleName(responsible.vehicle, responsible.plate) : assessment.at_fault_vehicle || 'À déterminer'}</strong></div><span className="aj-meta">Confiance {confidenceLabels[assessment.media.liability.confidence]?.toLowerCase() || 'à préciser'} · à valider</span></div>
          <div className="aj-video-actions">{assessment.selected_video_id && <button className="ir-button ir-secondary" onClick={() => void showVideo()}><span aria-hidden="true">▷</span> Voir la vidéo source</button>}</div>
          {video && <video className="aj-video" src={video} controls aria-label="Vidéo correspondante" onLoadedMetadata={event => { if (videoStart != null) event.currentTarget.currentTime = videoStart }} />}
        </section>
        <details className="ir-card aj-secondary-detail" aria-labelledby="vehicles-title">
          <summary id="vehicles-title">Véhicules impliqués <span>{vehicles.length}</span></summary>
          {vehicles.length === 0 && <p className="aj-meta">Véhicules impliqués à confirmer.</p>}
          <div className="aj-vehicles">{vehicles.map((plate, i) => {
            const responsibility = assessment.media.liability.vehicle_assessments?.find(v => v.vehicle === plate.vehicle)
            const insurer = review?.insurance_matches.find(v => v.vehicle === plate.vehicle)
            const sources = [...plate.citations, ...(responsibility?.citations || [])].filter((citation, index, all) =>
              all.findIndex(item => item.evidence_id === citation.evidence_id && item.timestamp_seconds === citation.timestamp_seconds) === index)
            return <article key={i} className="aj-vehicle">
              <span className="aj-label">{plate.role === 'insured' ? 'Assuré' : plate.role === 'third_party' ? 'Tiers' : 'Rôle à confirmer'}</span>
              <h3>{vehicleName(plate.vehicle, plate.plate)}</h3><span className="aj-plate">{plate.plate || 'Plaque illisible'}</span>
              <dl><div><dt>Assureur</dt><dd>{insurer?.data.insurer_name || 'Non identifié'}</dd></div>
                <div><dt>Responsabilité</dt><dd>{responsibilityLabels[responsibility?.assessment || ''] || 'À déterminer'}</dd></div>
              </dl>
              {sources.length > 0 && <details className="aj-details"><summary>Sources</summary>
                <div className="aj-sources">{sources.map((c, j) => <span key={j}>{cite(c.evidence_id, c.timestamp_seconds ?? null)}</span>)}</div>
              </details>}
            </article>
          })}</div>
        </details>
        <details className="ir-card aj-secondary-detail" aria-labelledby="damages-title">
          <summary id="damages-title">Dommages observés <span>{damages.length}</span></summary>
          <div className="aj-damages">{damages.length ? damages.map((d, i) => <article key={i}>
            <div className="aj-heading"><h3>{vehicleName(d.vehicle, vehicles.find(plate => plate.vehicle === d.vehicle)?.plate || null)}</h3><span className="aj-chip">{severityLabels[d.severity] || 'À préciser'}</span></div>
            <div className="aj-parts">{d.affected_parts.map((part, j) => <span key={j}>{part}</span>)}</div>
            <BulletPoints items={concisePoints(d.description)} />
            {d.accident_link !== 'consistent' && <span className="aj-meta aj-caution">{d.accident_link === 'inconsistent' ? 'Lien avec l’accident non établi' : 'Lien avec l’accident à confirmer'}</span>}
          </article>) : <p className="aj-meta">Aucun dommage documenté.</p>}</div>
        </details>
        {garageHelp && <section className="ir-card aj-garage-help" aria-labelledby="garages-title">
          <div className="aj-heading"><h2 id="garages-title">Garages à proximité</h2></div>
          {garageHelp}
        </section>}
      </div>
      <div className="aj-side">
        <section className="ir-card aj-repairs" aria-labelledby="repairs-title">
          <div className="aj-heading"><h2 id="repairs-title">Coût des réparations</h2>
            {estimate && review?.status === 'awaiting_review' && <button className="aj-edit-button" type="button"
              aria-label={editingEstimate ? 'Fermer la modification du chiffrage' : 'Modifier le chiffrage'}
              title={editingEstimate ? 'Fermer' : 'Modifier le chiffrage'} disabled={busy}
              onClick={() => { setEditingEstimate(!editingEstimate); setCostRanges(costItems.map(item => ({ minimum_minor: item.minimum_minor, maximum_minor: item.maximum_minor }))) }}>
              {editingEstimate ? '×' : <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m15 5 4 4M4 20l4.5-1 11-11a2.12 2.12 0 0 0-3-3l-11 11L4 20Z" /></svg>}
            </button>}
          </div>
          <RepairCostBreakdown assessment={assessment} />
          {editingEstimate && estimate && <div className="aj-cost-editor" aria-label="Modifier la fourchette du chiffrage">
            <h3>Modifier la fourchette</h3>
            {costItems.map((item, index) => {
              const current = costRanges[index] || item
              const upper = Math.min(1_000_000, Math.max(1000, Math.ceil(item.maximum_minor / 100) * 2))
              return <fieldset key={index} className="aj-cost-row"><legend>{'label' in item ? item.label : 'Estimation globale'}</legend>
                <div className="aj-cost-inputs">
                  <label>Minimum (€)<input type="number" min="0" max="1000000" step="0.01" value={toEuros(current.minimum_minor)}
                    onChange={event => updateCost(index, 'minimum_minor', toMinor(event.target.value))} /></label>
                  <label>Maximum (€)<input type="number" min="0" max="1000000" step="0.01" value={toEuros(current.maximum_minor)}
                    onChange={event => updateCost(index, 'maximum_minor', toMinor(event.target.value))} /></label>
                </div>
                <div className="aj-range-pair">
                  <input type="range" aria-label={`Minimum · ${'label' in item ? item.label : 'estimation globale'}`} min="0" max={Math.min(upper, current.maximum_minor / 100)} step="10" value={current.minimum_minor / 100}
                    onChange={event => updateCost(index, 'minimum_minor', toMinor(event.target.value))} />
                  <input type="range" aria-label={`Maximum · ${'label' in item ? item.label : 'estimation globale'}`} min={Math.max(0, current.minimum_minor / 100)} max={upper} step="10" value={current.maximum_minor / 100}
                    onChange={event => updateCost(index, 'maximum_minor', toMinor(event.target.value))} />
                </div>
              </fieldset>
            })}
            <p className="aj-cost-total">Nouvelle fourchette totale <strong>{money(costTotal.minimum_minor)} – {money(costTotal.maximum_minor)}</strong></p>
            {!validCosts && <p className="ir-error">Chaque maximum doit être supérieur ou égal au minimum.</p>}
            <button className="ir-button ir-primary" type="button" disabled={busy || !validCosts || !costsChanged}
              onClick={() => void saveEstimate()}>{busy ? 'Enregistrement…' : 'Enregistrer le chiffrage'}</button>
          </div>}
          {review?.status === 'awaiting_review' && <div className="aj-validation" id="review">
            <div className="aj-heading"><h3>Valider le montant</h3></div>
            <label className="ir-field">Montant retenu (€)<input type="number" min="0.01" max="1000000" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} disabled={editingEstimate || busy} /></label>
            {amended && <label className="ir-field">Motif de modification<textarea rows={2} value={reason} onChange={e => setReason(e.target.value)} /></label>}
            <button className="ir-button ir-primary aj-approve" disabled={busy || editingEstimate || !Number.isFinite(selectedAmount) || selectedAmount <= 0 || (amended && !reason.trim())} onClick={() => void approve()}>{busy ? 'Validation…' : 'Valider le montant'}</button>
          </div>}
          {review?.status === 'approved' && <div className="aj-approved-amount" role="status"><span>Montant validé</span><strong>{money(review.approved_amount_minor!)}</strong></div>}
          {review?.status === 'approved' && view && view.notifications.length === 0 && <div className="aj-send-panel">
            <div className="aj-delivery"><strong>Destinataires · {view.notification_mode === 'simulated' ? 'simulation' : 'envoi direct'}</strong>
              {(view.notification_preview || []).map((preview, index) => <span key={`${preview.channel}-${index}`}>{preview.channel === 'sms' ? 'Assuré · SMS' : 'Assureur tiers · email'} : {preview.recipient || 'Destinataire indisponible'}{preview.status === 'unavailable' ? ` · ${preview.reason || 'envoi indisponible'}` : ''}</span>)}
            </div>
            <button className="ir-button ir-primary aj-approve" type="button" disabled={busy || !view.notification_preview?.length || view.notification_preview.some(item => item.status !== 'ready')}
              onClick={() => void sendNotifications()}>{busy ? 'Envoi…' : view.notification_mode === 'simulated' ? 'Simuler l’envoi du SMS et de l’email' : 'Envoyer le SMS et l’email'}</button>
          </div>}
          {view && view.notifications.length > 0 && <div className="aj-delivery aj-delivery-status" id="notifications"><strong>Notifications · {view.notification_mode === 'simulated' ? 'simulation' : 'envoi direct'}</strong>{view.notifications.map(notification => <div key={notification.id}><span>{notification.channel === 'sms' ? 'SMS' : 'Email'} · {notification.recipient}</span><strong>{deliveryStatus(notification.status, notification.mode)}</strong>{notification.channel === 'sms' && notification.body && <p className="aj-notification-body">{notification.body}</p>}{notification.error_code && <small>{notification.error_code}</small>}</div>)}</div>}
        </section>
      </div>
    </div>}
    {(error || pollError) && <p role="alert" className="ir-error">{error || pollError}</p>}
  </div>
}
