import { useEffect, useMemo, useRef, useState } from 'react'
import type { CaseView, DraftView, TransmissionPreview } from '../lib/api'
import { ApiError, approveDraft, downloadTransmissionReceipt, getTransmissionPreview, simulateRegistration, simulateSend, updateCurrentDraft } from '../lib/api'
import { displayLabel, reasonLabel, hasCurrentApproval } from '../lib/presentation'
import { formatMoney, parseEuroCents } from '../lib/money'

type ReviewPanelProps = {
  caseView: CaseView
  accessToken: string
  onRefresh: () => void
}

type ReviewForm = {
  recipient: Record<string, unknown>
  recipientName: string
  organization: string
  email: string
  amount: string
  currency: string
  body: string
  transmissionComment: string
  attachmentIds: string[]
}

const requiredUpstreamGates = ['intake', 'counterparty', 'evidence']

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function recipientLabel(draft: DraftView): string {
  const recipient = record(draft.recipient)
  return text(recipient.name || recipient.correspondent_name || recipient.recipient_name || recipient.organization || recipient.insurer) || 'Destinataire à confirmer'
}

function formFromDraft(draft: DraftView): ReviewForm {
  const recipient = record(draft.recipient)
  const amount = draft.amount_minor === null ? '' : (draft.amount_minor / 100).toFixed(2)
  return {
    recipient,
    recipientName: text(recipient.name || recipient.correspondent_name || recipient.recipient_name),
    organization: text(recipient.organization || recipient.insurer || recipient.correspondent),
    email: text(recipient.email),
    amount,
    currency: draft.currency || '',
    body: draft.body,
    transmissionComment: draft.transmission_comment || '',
    attachmentIds: [...draft.attachment_ids],
  }
}

function moneyFromForm(value: string): number | null {
  if (!value.trim()) return null
  return parseEuroCents(value) ?? Number.NaN
}

export function ReviewPanel({ caseView, accessToken, onRefresh }: ReviewPanelProps) {
  const draft = caseView.current_draft
  const [form, setForm] = useState<ReviewForm | null>(null)
  const [busy, setBusy] = useState<'save' | 'approve' | 'registration' | 'send' | null>(null)
  const [notice, setNotice] = useState('')
  const [conflictForm, setConflictForm] = useState<ReviewForm | null>(null)
  const [reviewConfirmed, setReviewConfirmed] = useState(false)
  const [sendConfirmed, setSendConfirmed] = useState(false)
  const [preview, setPreview] = useState<TransmissionPreview | null>(null)
  const idempotencyKeys = useRef<Record<string, string>>({})

  useEffect(() => {
    setForm(draft ? formFromDraft(draft) : null)
    setNotice('')
    setReviewConfirmed(false)
    setSendConfirmed(false)
  }, [draft?.id, draft?.sha256])

  useEffect(() => {
    let live = true
    setPreview(null)
    if (draft) void getTransmissionPreview(accessToken, caseView.id)
      .then(value => { if (live) setPreview(value) })
      .catch(() => { if (live) setPreview(null) })
    return () => { live = false }
  }, [accessToken, caseView.id, caseView.content_revision, caseView.state_version, draft?.sha256])

  const originalForm = useMemo(() => draft ? formFromDraft(draft) : null, [draft?.id, draft?.sha256])
  const dirty = Boolean(form && originalForm && JSON.stringify(form) !== JSON.stringify(originalForm))
  const upstreamGates = caseView.gate_results.filter((gate) => gate.gate !== 'approval')
  const gatesPassed = requiredUpstreamGates.every((name) => upstreamGates.some((gate) => gate.gate === name && gate.status === 'passed'))
  const approvalBlock = caseView.gate_results.find(gate => gate.gate === 'approval' && gate.status === 'blocked')
  const amountMinor = form ? moneyFromForm(form.amount) : null
  const amountInvalid = Number.isNaN(amountMinor) || (amountMinor !== null && !form?.currency.trim()) || (amountMinor === null && Boolean(form?.currency.trim()))
  const recipientComplete = Boolean(form?.recipientName.trim() || form?.organization.trim())
  const draftComplete = Boolean(form && recipientComplete && form.body.trim() && !amountInvalid)
  const materialMatches = (!caseView.estimate || (amountMinor === caseView.estimate.total_minor && form?.currency === 'EUR'))
    && Boolean(caseView.estimate && caseView.quote && caseView.quote_status === 'matched' && form?.attachmentIds.includes(caseView.quote.evidence_id))
  const approvalMatches = hasCurrentApproval(caseView)
  const latestRegistration = [...caseView.actions].reverse().find((action) => action.kind === 'registration' && action.status === 'confirmed')
  const latestSend = [...caseView.actions].reverse().find((action) => action.kind === 'send' && action.status === 'confirmed')
  const unknownAction = caseView.actions.find(action => action.status === 'unknown')
  const previewCurrent = Boolean(preview && draft && preview.draft_id === draft.id && preview.draft_sha256 === draft.sha256 && preview.content_revision === caseView.content_revision)
  const changedFields = form && originalForm ? (Object.keys(form) as (keyof ReviewForm)[]).filter(key => JSON.stringify(form[key]) !== JSON.stringify(originalForm[key])) : []

  function changeForm<K extends keyof ReviewForm>(key: K, value: ReviewForm[K]) {
    setForm((current) => current ? { ...current, [key]: value } : current)
  }

  async function saveDraft() {
    if (!draft || !form || !dirty || !gatesPassed || busy) return
    if (!recipientComplete || !form.body.trim() || amountInvalid) {
      setNotice('Complétez le destinataire et le message. Renseignez ensemble le montant et la devise.')
      return
    }
    setBusy('save')
    setNotice('')
    const recipient = { ...form.recipient }
    const nameKey = ['name', 'correspondent_name', 'recipient_name'].find((key) => key in recipient) || 'name'
    const organizationKey = ['organization', 'insurer', 'correspondent'].find((key) => key in recipient) || 'organization'
    if (form.recipientName.trim()) recipient[nameKey] = form.recipientName.trim()
    else delete recipient[nameKey]
    if (form.organization.trim()) recipient[organizationKey] = form.organization.trim()
    else delete recipient[organizationKey]
    if (form.email.trim()) recipient.email = form.email.trim()
    else delete recipient.email
    try {
      await updateCurrentDraft(accessToken, caseView.id, caseView.state_version, {
        recipient: Object.keys(recipient).length ? recipient : null,
        amount_minor: amountMinor,
        currency: amountMinor === null ? null : form.currency.trim().toUpperCase(),
        body: form.body.trim(),
        attachment_ids: form.attachmentIds,
        transmission_comment: form.transmissionComment,
      })
      setNotice('Modifications enregistrées. Cette nouvelle version demande une nouvelle validation.')
      onRefresh()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setConflictForm(form)
        setNotice(`Conflit de version : le dossier est maintenant en version ${String(error.details.current_state_version || 'plus récente')}. Comparez et ressaisissez après rechargement.`)
        onRefresh()
      } else setNotice(error instanceof Error ? error.message : 'Impossible d’enregistrer la proposition.')
    } finally {
      setBusy(null)
    }
  }

  async function approve() {
    if (!draft || !draftComplete || !materialMatches || dirty || !gatesPassed || approvalBlock || busy || approvalMatches || !reviewConfirmed) return
    setBusy('approve')
    setNotice('')
    try {
      await approveDraft(accessToken, caseView.id, draft.id, draft.sha256, caseView.state_version)
      setNotice('Cette version est validée. Vous pouvez préparer la transmission simulée.')
      onRefresh()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Impossible de valider cette proposition.')
    } finally {
      setBusy(null)
    }
  }

  async function simulateAction(kind: 'registration' | 'send') {
    if (!draft || !approvalMatches || busy || (kind === 'send' && (!previewCurrent || !sendConfirmed))) return
    const key = `${caseView.id}:${kind}`
    const idempotencyKey = idempotencyKeys.current[key] || crypto.randomUUID()
    idempotencyKeys.current[key] = idempotencyKey
    setBusy(kind)
    setNotice('')
    try {
      const receipt = kind === 'registration'
        ? await simulateRegistration(accessToken, caseView.id, caseView.state_version, idempotencyKey)
        : await simulateSend(accessToken, caseView.id, caseView.state_version, idempotencyKey)
      if (receipt.status !== 'confirmed') {
        setNotice(`Résultat ${receipt.status} pour cette action simulée. Une réconciliation est nécessaire avant de continuer.`)
        onRefresh()
        return
      }
      delete idempotencyKeys.current[key]
      setNotice(`${kind === 'registration' ? 'Préparation' : 'Envoi simulé'} enregistré : ${receipt.reference || 'reçu en attente'}.`)
      onRefresh()
    } catch (error) {
      setNotice(error instanceof Error ? `${error.message} Une nouvelle tentative reprend la même opération.` : 'Résultat incertain. Réessayez pour récupérer le reçu de cette opération.')
    } finally {
      setBusy(null)
    }
  }

  if (!draft || !form) {
    return <section className="panel review-panel"><div className="panel-header"><div><p className="eyebrow">VALIDATION HUMAINE</p><h2>Proposition de recours</h2><p className="panel-description">La proposition sera disponible une fois l’analyse terminée et les points bloquants résolus.</p></div><span className="soon-badge">EN ATTENTE</span></div><div id="transmission"><p className="simulation-note">La transmission sera disponible après validation du recours.</p></div></section>
  }

  const hasDraftEdit = caseView.status !== 'registered' && caseView.status !== 'sent'
  const canApprove = gatesPassed && !approvalBlock && draftComplete && materialMatches && !dirty && hasDraftEdit && !approvalMatches
  const canRegister = approvalMatches && caseView.status === 'approved' && !latestRegistration
  const canSend = approvalMatches && caseView.status === 'registered' && Boolean(latestRegistration) && !latestSend

  return (
    <section className="panel review-panel" aria-labelledby="review-title">
      <div className="panel-header">
        <div><p className="eyebrow">VALIDATION HUMAINE</p><h2 id="review-title">Vérifier et valider le recours</h2><p className="panel-description">Vérifiez le destinataire, le montant et le message. Toute modification enregistrée demande une nouvelle validation.</p></div>
        <span className="draft-version-badge">VERSION {draft.version}</span>
      </div>
      {!gatesPassed && <p className="review-validation">Certains points restent à résoudre. <a href="#analysis">Voir les contrôles du dossier</a>.</p>}
      {approvalBlock && <p className="review-validation">{approvalBlock.reason_codes.map(reasonLabel).join(' ') || 'Des points restent à résoudre avant validation.'}</p>}

      <div className="review-form">
        <div className="review-fields">
          <label className="field"><span>Destinataire</span><input value={form.recipientName} onChange={(event) => changeForm('recipientName', event.target.value)} disabled={!hasDraftEdit || Boolean(busy)} placeholder="Nom du destinataire" /></label>
          <label className="field"><span>Organisme</span><input value={form.organization} onChange={(event) => changeForm('organization', event.target.value)} disabled={!hasDraftEdit || Boolean(busy)} placeholder="Assureur ou correspondant" /></label>
          <label className="field"><span>Email du destinataire</span><input type="email" value={form.email} onChange={(event) => changeForm('email', event.target.value)} disabled={!hasDraftEdit || Boolean(busy)} placeholder="Adresse fictive (facultatif)" /></label>
          <label className="field"><span>Montant proposé</span><input type="number" min="0" step="0.01" value={form.amount} onChange={(event) => changeForm('amount', event.target.value)} disabled={!hasDraftEdit || Boolean(busy)} placeholder="Facultatif" /></label>
          <label className="field"><span>Devise</span><input maxLength={3} value={form.currency} onChange={(event) => changeForm('currency', event.target.value.toUpperCase())} disabled={!hasDraftEdit || Boolean(busy)} placeholder="EUR" /></label>
          <label className="field field-wide"><span>Message de recours</span><textarea rows={6} value={form.body} onChange={(event) => changeForm('body', event.target.value)} disabled={!hasDraftEdit || Boolean(busy)} /></label>
          <label className="field field-wide"><span>Commentaire transmis</span><textarea id="transmission-comment" rows={3} value={form.transmissionComment} onChange={(event) => changeForm('transmissionComment', event.target.value)} disabled={!hasDraftEdit || Boolean(busy)} placeholder="Note incluse dans la proposition transmise" /></label>
        </div>
        <fieldset className="review-attachments" disabled={!hasDraftEdit || Boolean(busy)}>
          <legend>Pièces jointes</legend>
          {caseView.evidence.length === 0 ? <p className="muted-copy">Aucune pièce disponible.</p> : caseView.evidence.map((item) => <label key={item.id}>
            <input type="checkbox" checked={form.attachmentIds.includes(item.id)} disabled={item.mime_type === 'application/pdf' && item.id !== caseView.quote?.evidence_id} onChange={(event) => changeForm('attachmentIds', event.target.checked ? [...form.attachmentIds, item.id] : form.attachmentIds.filter((id) => id !== item.id))} />
            <span>{displayLabel(item.role || item.kind)}{item.mime_type === 'application/pdf' ? ' · PDF' : ''}</span>
          </label>)}
        </fieldset>
      </div>

      {dirty && <div className="review-dirty-note"><strong>Aperçu des différences avant sauvegarde</strong><ul>{changedFields.filter(key => key !== 'recipient').map(key => <li key={key}><strong>{{ recipientName: 'Destinataire', organization: 'Organisme', email: 'Email', amount: 'Montant', currency: 'Devise', body: 'Message', transmissionComment: 'Note de transmission', attachmentIds: 'Pièces jointes', recipient: 'Destinataire' }[key]}</strong><div><span>Avant : </span>{key === 'attachmentIds' ? originalForm?.attachmentIds.map(id => caseView.evidence.find(e => e.id === id)?.original_filename || 'Pièce du dossier').join(', ') : String(originalForm?.[key] || '—')}</div><div><span>Après : </span>{key === 'attachmentIds' ? form.attachmentIds.map(id => caseView.evidence.find(e => e.id === id)?.original_filename || 'Pièce du dossier').join(', ') : String(form[key] || '—')}</div></li>)}</ul><p>Enregistrez pour préparer une nouvelle version à valider.</p></div>}
      {conflictForm && <div className="review-notice" role="alert"><strong>Conflit entre onglets.</strong><p>Version courante V{draft.version}. Comparez les champs modifiés ci-dessus. Vos valeurs précédentes restent disponibles sans écraser le serveur.</p><button className="button secondary" type="button" onClick={() => { setForm(conflictForm); setConflictForm(null) }}>Ressaisir mes valeurs sur la version courante</button><button className="button secondary" type="button" onClick={() => { setConflictForm(null); setForm(formFromDraft(draft)) }}>Garder la version courante</button></div>}
      {notice && <div className="review-notice" role="status">{notice}</div>}
      {amountInvalid && <p className="review-validation">Saisissez un montant positif ou nul avec une devise à trois lettres, ou videz les deux champs.</p>}
      {!materialMatches && <p className="review-validation">Le montant du brouillon et le devis doivent correspondre à l’estimation actuelle ; réanalysez le dossier après modification.</p>}

      <section className="review-package-preview" id="transmission" aria-label="Aperçu du package transmis">
        <h3>Proposition transmise · simulation</h3>
        {previewCurrent && preview ? <>
          <p>Version {draft.version} · {approvalMatches ? 'Validée' : 'À valider'}</p>
          <p><strong>Destinataire :</strong> {recipientLabel(draft)} · <strong>Référence simulée :</strong> {preview.registration_reference || 'à créer'}</p>
          <p><strong>Montant :</strong> {preview.amount_minor == null ? '—' : formatMoney(preview.amount_minor, preview.currency || 'EUR')}</p>
          <p><strong>Message :</strong> {preview.body}</p>
          {preview.transmission_comment && <p><strong>Commentaire :</strong> {preview.transmission_comment}</p>}
          <p><strong>Compte rendu :</strong> {preview.package.report_lines.length} lignes · <strong>Devis :</strong> {preview.package.quote?.filename || 'absent'}</p>
          <ul>{preview.attachments.map(item => <li key={item.id}>{item.filename || displayLabel(item.kind)}</li>)}</ul>
        </> : <p>Aperçu périmé ou indisponible : rechargez le dossier avant envoi.</p>}
      </section>

      <label className="review-confirmation"><input type="checkbox" checked={reviewConfirmed} onChange={event => setReviewConfirmed(event.target.checked)} disabled={Boolean(busy) || dirty || !previewCurrent} /> J’ai examiné le récit, ses sources, les pièces, le devis et son total ainsi que le destinataire de cette version.</label>
      <label className="review-confirmation"><input type="checkbox" checked={sendConfirmed} onChange={event => setSendConfirmed(event.target.checked)} disabled={Boolean(busy) || !previewCurrent || !approvalMatches} /> J’ai lu cet aperçu et confirme l’envoi simulé de cette version.</label>
      {unknownAction && <p className="review-validation">Action {unknownAction.kind} au résultat inconnu : une réconciliation est requise avant une nouvelle tentative.</p>}

      <div className="review-controls">
        {dirty ? <button className="button primary" type="button" onClick={() => void saveDraft()} disabled={Boolean(busy) || !gatesPassed}>{busy === 'save' ? 'Enregistrement…' : 'Enregistrer les modifications'}</button>
          : !approvalMatches ? <button className="button primary" type="button" onClick={() => void approve()} disabled={!canApprove || !reviewConfirmed || !previewCurrent || Boolean(busy)}>{busy === 'approve' ? 'Validation…' : 'Valider cette version'}</button>
          : canRegister ? <button className="button primary" type="button" onClick={() => void simulateAction('registration')} disabled={Boolean(busy) || Boolean(unknownAction)}>{busy === 'registration' ? 'Préparation…' : 'Préparer la transmission simulée'}</button>
          : canSend ? <button className="button primary" type="button" onClick={() => void simulateAction('send')} disabled={Boolean(busy) || Boolean(unknownAction) || !sendConfirmed || !previewCurrent}>{busy === 'send' ? 'Transmission…' : 'Confirmer l’envoi simulé'}</button>
          : <p className="review-complete" role="status">{caseView.status === 'sent' ? 'Envoi simulé enregistré.' : 'Version validée.'}</p>}
        {latestSend && <button className="button secondary" type="button" onClick={() => void downloadTransmissionReceipt(accessToken, caseView.id).catch(error => setNotice(error instanceof Error ? error.message : 'Reçu indisponible'))}>Télécharger le reçu JSON</button>}
      </div>
      <details className="ir-details review-trace">
        <summary>Suivi de la validation et de l’envoi</summary>
        <div className="draft-meta"><span>Proposition : version {draft.version}</span><span>Destinataire : {recipientLabel(draft)}</span><span>{approvalMatches ? 'Validation actuelle' : 'Validation requise'}</span></div>
        {latestRegistration && <p>Enregistrement : {latestRegistration.reference || 'Reçu disponible'}</p>}
        {latestSend && <p>Envoi : {latestSend.reference || 'Reçu disponible'}</p>}
        <ul>{caseView.gate_results.map(gate => <li key={gate.gate}>{displayLabel(gate.gate)} : {displayLabel(gate.status)}{gate.reason_codes.length > 0 && <span> · {gate.reason_codes.map(reasonLabel).join(' ')}</span>}</li>)}</ul>
      </details>
      <p className="simulation-note">Transmission simulée : aucun message n’est envoyé à un assureur.</p>
    </section>
  )
}
