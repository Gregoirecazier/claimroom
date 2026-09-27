import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createEvidenceUploadIntent, finalizeEvidence, getCase, updateIntake } from '../lib/api'
import type { CaseView, EvidenceKind, IntakePatch } from '../lib/api'
import { supabase } from '../lib/supabase'
import { readConversation, saveConversation, type ConversationMessage } from '../lib/conversation'

type Step = 'location' | 'incident_at' | 'injury_status' | 'danger_status' | 'insured_reference' | 'policy_reference' | 'insured_vehicle' | 'insured_plate' | 'scene_photo' | 'damage_photo' | 'details'

const questions: Record<Step, string> = {
  location: 'Pouvez-vous me préciser le lieu de l’accident ?',
  incident_at: 'À quelle date et à quelle heure l’accident a-t-il eu lieu ? (JJ/MM/AAAA HH:MM)',
  injury_status: 'Y a-t-il des blessés ?',
  danger_status: 'La situation présente-t-elle encore un danger ?',
  insured_reference: 'Avez-vous une référence de sinistre ou de dossier ? Vous pouvez passer si vous ne l’avez pas.',
  policy_reference: 'Avez-vous votre référence de contrat ? Vous pouvez aussi passer cette question.',
  insured_vehicle: 'Quel est le modèle de votre véhicule assuré ? Vous pouvez passer si vous ne le savez pas.',
  insured_plate: 'Quelle est l’immatriculation de votre véhicule assuré ? Vous pouvez passer si vous ne l’avez pas.',
  scene_photo: 'Pouvez-vous joindre une photo d’ensemble du lieu et des véhicules ?',
  damage_photo: 'Pouvez-vous joindre une photo rapprochée des dégâts ?',
  details: 'Merci. Votre dossier est à jour. Vous pouvez ajouter une précision ou une autre photo ici.',
}

const optionalSteps: Step[] = ['insured_reference', 'policy_reference', 'insured_vehicle', 'insured_plate', 'scene_photo', 'damage_photo']

function nextStep(view: CaseView, skipped: Step[] = []): Step {
  const intake = view.intake
  if (!intake.location) return 'location'
  if (!intake.incident_at) return 'incident_at'
  if (!intake.injury_status || intake.injury_status === 'unknown') return 'injury_status'
  if (!intake.danger_status || intake.danger_status === 'unknown') return 'danger_status'
  if (!intake.insured_reference && !skipped.includes('insured_reference')) return 'insured_reference'
  if (!intake.policy_reference && !skipped.includes('policy_reference')) return 'policy_reference'
  if (!intake.insured_vehicle && !skipped.includes('insured_vehicle')) return 'insured_vehicle'
  if (!intake.insured_plate && !skipped.includes('insured_plate')) return 'insured_plate'
  if (!skipped.includes('scene_photo') && !view.evidence.some(item => item.kind === 'scene_photo')) return 'scene_photo'
  if (!skipped.includes('damage_photo') && !view.evidence.some(item => item.kind === 'damage_photo')) return 'damage_photo'
  return 'details'
}

function incidentDate(value: string): string | null {
  const match = value.trim().match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})\s+(\d{1,2}):(\d{2})$/)
  if (!match) return null
  const [, day, month, year, hour, minute] = match
  const date = new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute))
  if (date.getFullYear() !== Number(year) || date.getMonth() !== Number(month) - 1 ||
      date.getDate() !== Number(day) || date.getHours() !== Number(hour) || date.getMinutes() !== Number(minute)) return null
  return date.toISOString()
}

const photoKinds: { value: EvidenceKind; label: string }[] = [
  { value: 'scene_photo', label: 'Vue d’ensemble' },
  { value: 'damage_photo', label: 'Dégâts' },
  { value: 'vehicle_photo', label: 'Véhicule' },
]

export function SimulatedWhatsAppChat({ caseId, accessToken }: { caseId: string; accessToken: string }) {
  const [view, setView] = useState<CaseView | null>(null)
  const [messages, setMessages] = useState<ConversationMessage[]>(() => readConversation(caseId))
  const [draft, setDraft] = useState('')
  const [skippedSteps, setSkippedSteps] = useState<Step[]>([])
  const [photoKind, setPhotoKind] = useState<EvidenceKind>('scene_photo')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const id = useRef(Math.max(0, ...readConversation(caseId).map(item => item.id)))
  const listEnd = useRef<HTMLDivElement | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)

  function append(side: ConversationMessage['side'], text: string, evidenceId?: string) {
    setMessages(current => [...current, { id: ++id.current, side, text, createdAt: new Date().toISOString(), evidenceId }])
  }

  useEffect(() => {
    let active = true
    setLoading(true); setError(''); setSkippedSteps([])
    void getCase(accessToken, caseId).then(result => {
      if (!active) return
      setView(result)
      setPhotoKind(nextStep(result) === 'damage_photo' ? 'damage_photo' : 'scene_photo')
      const saved = readConversation(caseId)
      if (saved.length) { id.current = Math.max(...saved.map(item => item.id)); setMessages(saved) }
      else setMessages([
        { id: ++id.current, side: 'agent', text: 'Bonjour, ici Claimroom insurance. Nous poursuivons votre déclaration après l’appel. Cette conversation WhatsApp est simulée.', createdAt: new Date().toISOString() },
        { id: ++id.current, side: 'agent', text: questions[nextStep(result)], createdAt: new Date().toISOString() },
      ])
    }).catch(() => { if (active) setError('Impossible d’ouvrir le dossier. Réessayez en rechargeant la page.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [accessToken, caseId])

  useEffect(() => { if (messages.length) saveConversation(caseId, messages) }, [caseId, messages])

  useEffect(() => { listEnd.current?.scrollIntoView?.({ behavior: 'smooth' }) }, [messages])

  const step = view ? nextStep(view, skippedSteps) : null
  const photoStep = step === 'scene_photo' || step === 'damage_photo'

  async function sendText(event: FormEvent) {
    event.preventDefault()
    if (!view || !step || busy || !draft.trim() || photoStep) return
    const answer = draft.trim()
    let patch: IntakePatch
    if (step === 'incident_at') {
      const parsed = incidentDate(answer)
      if (!parsed) { setError('Indiquez une date valide au format JJ/MM/AAAA HH:MM.'); return }
      patch = { incident_at: parsed }
    } else if (step === 'injury_status' || step === 'danger_status') {
      if (!/^(oui|non)$/i.test(answer)) { setError('Répondez par oui ou non.'); return }
      patch = { [step]: /^oui$/i.test(answer) ? 'yes' : 'no' }
    } else if (step === 'details') {
      patch = { narrative: `${view.intake.narrative.trim()}\nComplément après l’appel : ${answer}`.trim() }
    } else {
      patch = { [step]: answer }
    }
    setBusy(true); setError('')
    try {
      const updated = await updateIntake(accessToken, caseId, view.state_version, patch)
      setView(updated); setDraft('')
      append('you', answer)
      const next = nextStep(updated, skippedSteps)
      setPhotoKind(next === 'damage_photo' ? 'damage_photo' : 'scene_photo')
      const urgent = (step === 'injury_status' || step === 'danger_status') && /^oui$/i.test(answer)
      append('agent', step === 'details' ? 'Précision ajoutée au dossier. Vous pouvez en envoyer une autre.' :
        `${urgent ? 'Si une personne est blessée ou encore en danger, contactez immédiatement les secours. ' : ''}Merci, c’est enregistré. ${questions[next]}`)
    } catch { setError('Ce message n’a pas été enregistré. Réessayez ou rechargez le dossier.') }
    finally { setBusy(false) }
  }

  function skip() {
    if (!view || !step) return
    if (!optionalSteps.includes(step)) return
    const nextSkipped = [...skippedSteps, step]
    setSkippedSteps(nextSkipped)
    append('you', step === 'scene_photo' || step === 'damage_photo' ? 'Je n’ai pas cette photo pour le moment.' : 'Je ne l’ai pas sous la main.')
    append('agent', `D’accord, ce point restera à compléter dans le dossier. ${questions[nextStep(view, nextSkipped)]}`)
  }

  async function upload(file: File) {
    if (!view || !supabase || busy) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size === 0 || file.size > 5_242_880) {
      setError('Choisissez une photo JPEG, PNG ou WebP de 5 Mo maximum.'); return
    }
    setBusy(true); setError('')
    try {
      const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
      const sha = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
      const intent = await createEvidenceUploadIntent(accessToken, caseId, {
        filename: file.name, mime_type: file.type, byte_size: file.size, client_sha256: sha,
        kind: photoKind, expected_state_version: view.state_version,
      })
      const result = await supabase.storage.from(intent.bucket).uploadToSignedUrl(intent.storage_path, intent.token, file, {
        contentType: file.type, upsert: false,
      })
      if (result.error && !result.error.message.toLowerCase().includes('already exists')) throw result.error
      const updated = await finalizeEvidence(accessToken, caseId, intent.storage_path, sha, photoKind, view.state_version)
      setView(updated)
      const added = updated.evidence.find(item => !view.evidence.some(previous => previous.id === item.id))
      append('you', file.name, added?.id)
      const next = nextStep(updated, skippedSteps)
      setPhotoKind(next === 'damage_photo' ? 'damage_photo' : 'scene_photo')
      append('agent', `Photo reçue et ajoutée au dossier. ${questions[next]}`)
    } catch { setError('La photo n’a pas été ajoutée. Réessayez avec le même fichier.') }
    finally { setBusy(false); if (fileInput.current) fileInput.current.value = '' }
  }

  return <section className="voice-test-whatsapp" aria-label="Conversation WhatsApp simulée">
    <div className="voice-test-whatsapp-head"><span className="voice-test-whatsapp-avatar">C</span><div><strong>Claimroom insurance</strong><small>Agent · simulation WhatsApp</small></div><span className="voice-test-whatsapp-dots" aria-hidden="true">•••</span></div>
    <div className="voice-test-whatsapp-feed" role="log" aria-live="polite">
      <p className="voice-test-whatsapp-disclaimer">Démo dans le navigateur · aucun message WhatsApp réel n’est envoyé</p>
      {loading && <p className="voice-test-whatsapp-empty">Ouverture du dossier…</p>}
      {messages.map(message => <div key={message.id} className={`voice-test-whatsapp-bubble ${message.side}`}>
        {message.evidenceId && <span aria-hidden="true">📎 </span>}{message.text}
      </div>)}
      <div ref={listEnd} />
    </div>
    {error && <p className="voice-test-whatsapp-error" role="alert">{error}</p>}
    {view && <div className="voice-test-whatsapp-compose">
      {photoStep && <p className="voice-test-whatsapp-tip">Choisissez le type de photo, puis joignez-la au dossier.</p>}
      {(photoStep || step === 'details') && <div className="voice-test-whatsapp-photo-row">
        <label>Type de photo <select value={photoKind} disabled={busy} onChange={event => setPhotoKind(event.target.value as EvidenceKind)}>
          {photoKinds.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select></label>
        <button type="button" disabled={busy} onClick={() => fileInput.current?.click()}>Joindre une photo</button>
        <input ref={fileInput} className="voice-test-visually-hidden" aria-label="Choisir une photo" type="file" accept="image/jpeg,image/png,image/webp" onChange={event => { const file = event.target.files?.[0]; if (file) void upload(file) }} />
      </div>}
      {step && optionalSteps.includes(step) && <button type="button" className="voice-test-whatsapp-skip" disabled={busy} onClick={skip}>{photoStep ? 'Je n’ai pas cette photo' : 'Je ne l’ai pas'}</button>}
      {(step === 'injury_status' || step === 'danger_status') && <div className="voice-test-whatsapp-choices">
        <button type="button" disabled={busy} onClick={() => setDraft('Oui')}>Oui</button>
        <button type="button" disabled={busy} onClick={() => setDraft('Non')}>Non</button>
      </div>}
      {!photoStep && <form onSubmit={event => void sendText(event)}>
        <input aria-label="Votre message" value={draft} disabled={busy} maxLength={step === 'details' ? 500 : 200}
          placeholder={step === 'incident_at' ? 'JJ/MM/AAAA HH:MM' : step === 'injury_status' || step === 'danger_status' ? 'Oui ou non' : 'Votre réponse…'}
          onChange={event => setDraft(event.target.value)} />
        <button type="submit" disabled={busy || !draft.trim()}>Envoyer</button>
      </form>}
    </div>}
  </section>
}
