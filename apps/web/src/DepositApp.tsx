import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent, KeyboardEvent } from 'react'
import { supabase } from './lib/supabase'
import { guestRequest } from './lib/depositTransport'
import { HistoryWriter } from './lib/depositHistory'
import type { AssistantMessage, ChatProposal, ChatHistory, Phase } from './lib/depositHistory'
import './deposit.css'

const fileTypes = 'image/jpeg,image/png,image/webp,application/pdf,video/mp4'

type Evidence = {
  id: string; kind: string; source_kind: string; mime_type: string; byte_size: number
  checksum_status: string; original_filename: string | null; received_at: string
}
type Summary = {
  case_id: string; state_version: number; content_revision: number; source_label: string
  intake: Record<string, string | null>; missing_fields: string[]
  analysis_status?: string | null; analysis_requests?: string[]
  transcript_available: boolean; recording_status: string; evidence: Evidence[]; conversation_available?: boolean
}
type ChatResponse = { reply: string; proposal: ChatProposal | null }
type UploadIntent = { bucket: string; storage_path: string; token: string; expires_at: string }
type Pending = { intent: UploadIntent; sha: string; kind: string; file: File; stored?: boolean }
type QueuedFile = { id: string; file: File; preview?: string; status: 'ready' | 'uploading' | 'failed'; error?: string; pending?: Pending }
type ThreadItem = {
  id: string; sender: 'insured' | 'assistant'; text: string; created_at: string
  evidence?: Evidence
}

const fieldLabels: Record<string, string> = {
  insured_name: 'Nom et prénom',
  insured_reference: 'Référence du contrat', incident_at: 'Date et heure de l’accident',
  location: 'Lieu', narrative: 'Ce qui s’est passé', danger_status: 'Situation dangereuse',
  injury_status: 'Blessures', vehicle_country: 'Pays du véhicule',
}
const kindLabels: Record<string, string> = {
  scene_photo: 'Photo', vehicle_photo: 'Photo du véhicule', damage_photo: 'Photo des dommages',
  document: 'Document', scene_video: 'Vidéo', other: 'Pièce jointe',
}
const photoRequest = 'Pouvez-vous joindre des photos de votre véhicule et des dégâts avec le bouton + ? Une vue d’ensemble et des gros plans des dommages nous seront utiles.'
const closingMessage = 'Votre ajout de pièces est terminé. Vous pouvez encore envoyer un message, corriger une information ou ajouter une photo.'
const sessionKey = 'claimroom.deposit.session.v1'
type SavedSession = { token: string; caseId: string; phase: Phase; opening: string; openingAt: string; messages: AssistantMessage[]; proposal: ChatProposal | null; input: string; unsentFiles?: string[] }
function readSession(): SavedSession | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(sessionKey) || 'null') as SavedSession | null
    return saved && typeof saved.token === 'string' && typeof saved.caseId === 'string' && Array.isArray(saved.messages) && ['confirm', 'correct', 'collect', 'photos', 'complete'].includes(saved.phase) ? saved : null
  } catch { return null }
}
function saveSession(value: SavedSession | null) {
  try { if (value) sessionStorage.setItem(sessionKey, JSON.stringify(value)); else sessionStorage.removeItem(sessionKey) } catch { /* Storage may be unavailable in private browsing. */ }
}
function isAccessError(error: unknown) {
  return error instanceof Error && 'code' in error && ['expired_grant', 'revoked_grant', 'expired_session', 'invalid_grant', 'invalid_session'].includes(String(error.code))
}

function hasPhotos(summary: Summary): boolean {
  return summary.evidence.some(item => item.mime_type.startsWith('image/'))
}

function displayValue(field: string, value: string): string {
  if (field === 'incident_at') {
    const date = new Date(value)
    if (!Number.isNaN(date.getTime())) return new Intl.DateTimeFormat('fr-FR', {
      dateStyle: 'long', timeStyle: 'short', timeZone: 'Europe/Paris',
    }).format(date)
  }
  if (field === 'danger_status') return value === 'yes' ? 'Oui' : value === 'no' ? 'Non' : 'À confirmer'
  if (field === 'injury_status') return value === 'yes' ? 'Oui' : value === 'no' ? 'Non' : 'À confirmer'
  return value
}

function caseRecap(summary: Summary): string {
  const facts = Object.entries(fieldLabels)
    .filter(([field]) => field !== 'time_source')
    .flatMap(([field, label]) => {
      const value = summary.intake[field]
      return value ? [`• ${label} : ${displayValue(field, value)}`] : []
    })
  return `Bonjour, voici le récapitulatif de votre dossier :\n${facts.length ? facts.join('\n') : 'Aucune information n’est encore enregistrée.'}\n\nPouvez-vous confirmer que ces informations sont exactes ?`
}

function nextQuestion(summary: Summary): { field: string; text: string } | null {
  if (!summary.intake.insured_name?.trim()) return {
    field: 'insured_name', text: 'Quels sont vos nom et prénom ?',
  }
  return summary.intake.incident_at ? null : {
    field: 'incident_at', text: 'À quelle date et à quelle heure l’accident a-t-il eu lieu ?',
  }
}

function proposalLabel(proposal: ChatProposal): string {
  if (proposal.value === 'yes') return 'Oui'
  if (proposal.value === 'no') return 'Non'
  if (proposal.value === 'unknown') return 'Je ne sais pas'
  if (proposal.field === 'incident_at') {
    const date = new Date(proposal.value)
    if (!Number.isNaN(date.getTime())) return new Intl.DateTimeFormat('fr-FR', {
      dateStyle: 'long', timeStyle: 'short', timeZone: 'Europe/Paris',
    }).format(date)
  }
  return proposal.value
}

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

function kindFor(file: File): string {
  if (file.type === 'video/mp4') return 'scene_video'
  if (file.type === 'application/pdf') return 'document'
  return 'scene_photo'
}

function validateFile(file: File): string | null {
  if (!fileTypes.split(',').includes(file.type)) return 'Choisissez une photo JPG, PNG ou WebP, un PDF ou une vidéo MP4.'
  const maxBytes = file.type === 'video/mp4' ? 10_000_000 : 5_000_000
  if (file.size > maxBytes) return file.type === 'video/mp4'
    ? 'La vidéo doit faire 10 Mo maximum.' : 'Le fichier doit faire 5 Mo maximum.'
  return null
}

export default function DepositApp() {
  const initialLink = useRef(new URLSearchParams(window.location.hash.replace(/^#/, '')).get('token'))
  const started = useRef(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const messageKey = useRef<string | null>(null)
  const historyWriter = useRef<HistoryWriter | null>(null)
  const [historySaving, setHistorySaving] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [session, setSession] = useState('')
  const [summary, setSummary] = useState<Summary | null>(null)
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([])
  const [opening, setOpening] = useState('')
  const [openingAt, setOpeningAt] = useState('')
  const [phase, setPhase] = useState<Phase>('confirm')
  const [input, setInput] = useState('')
  const [files, setFiles] = useState<QueuedFile[]>([])
  const filesRef = useRef<QueuedFile[]>([])
  const uploadLock = useRef(false)
  const [proposal, setProposal] = useState<ChatProposal | null>(null)
  const [fallback, setFallback] = useState(false)
  const [fallbackField, setFallbackField] = useState('incident_at')
  const [fallbackValue, setFallbackValue] = useState('')
  const [busy, setBusy] = useState('opening')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    if (started.current) return
    started.current = true
    const link = initialLink.current
    initialLink.current = null
    window.history.replaceState(null, '', '/depot')
    const saved = readSession()
    if (!link && !saved?.token) {
      setBusy('')
      setError('Ouvrez le lien privé reçu par SMS pour accéder à votre conversation.')
      return
    }
    void (async () => {
      const token = link
        ? (await guestRequest<{ session_token: string }>('/v1/deposit/session', link)).session_token
        : saved!.token
      const caseSummary = await guestRequest<Summary>('/v1/deposit/summary', token)
      const history = await guestRequest<ChatHistory>('/v1/deposit/chat-history', token)
      historyWriter.current = new HistoryWriter(token, history.revision, history.state, (saving, message) => {
        setHistorySaving(saving); setHistoryError(message)
      })
      setSession(token)
      setSummary(caseSummary)
      // Server history survives a new tab, expired session, or renewed SMS link.
      // Import the old same-case tab history only when no server history exists.
      const restored = history.state || (saved?.caseId === caseSummary.case_id ? saved : null)
      if (restored) {
        setPhase(restored.phase)
        setOpening(restored.opening)
        setOpeningAt(restored.openingAt)
        setAssistantMessages(restored.messages)
        setProposal(restored.proposal)
      } else {
        setOpening(caseRecap(caseSummary))
        setOpeningAt(new Date().toISOString())
      }
      if (saved?.caseId === caseSummary.case_id) {
        setInput(saved.input)
        if (saved.unsentFiles?.length) setNotice(`Ces pièces n’avaient pas fini d’être envoyées. Sélectionnez-les à nouveau : ${saved.unsentFiles.join(", ")}.`)
      }
    })().catch(err => { if (isAccessError(err)) saveSession(null); setError(err instanceof Error ? err.message : 'Impossible d’ouvrir le lien.') })
      .finally(() => setBusy(''))
  }, [])

  useEffect(() => {
    if (session && summary && openingAt) saveSession({ token: session, caseId: summary.case_id, phase,
      opening, openingAt, messages: assistantMessages, proposal, input, unsentFiles: files.map(item => item.file.name) })
  }, [session, summary, phase, opening, openingAt, assistantMessages, proposal, input, files])

  useEffect(() => {
    if (session && openingAt) historyWriter.current?.enqueue({ phase, opening, openingAt,
      messages: assistantMessages, proposal })
  }, [session, phase, opening, openingAt, assistantMessages, proposal])

  useEffect(() => {
    if (!historySaving && !historyError && !busy) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [historySaving, historyError, busy])

  useEffect(() => { filesRef.current = files }, [files])
  useEffect(() => () => { filesRef.current.forEach(item => { if (item.preview) URL.revokeObjectURL(item.preview) }) }, [])

  function handleError(err: unknown, fallbackMessage: string) {
    if (isAccessError(err)) { saveSession(null); setSession(''); setSummary(null) }
    setError(err instanceof Error ? err.message : fallbackMessage)
  }

  useEffect(() => {
    if (!session) return
    let active = true
    const refresh = () => {
      if (document.visibilityState === 'hidden') return
      void guestRequest<Summary>('/v1/deposit/summary', session)
        .then(next => {
          if (active) setSummary(current => current && (current.content_revision > next.content_revision ||
            (current.content_revision === next.content_revision && current.state_version > next.state_version)) ? current : next)
        }).catch(err => { if (active && isAccessError(err)) handleError(err, 'Session expirée.') })
    }
    const timer = window.setInterval(refresh, 8000)
    window.addEventListener('focus', refresh)
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', refresh) }
  }, [session])

  const thread = useMemo(() => {
    const items: ThreadItem[] = [
      ...assistantMessages.map(message => ({
        id: message.id, sender: message.role, text: message.text, created_at: message.created_at,
      })),
      ...(summary?.evidence || []).map(item => ({
        id: item.id, sender: 'insured' as const, text: item.original_filename || kindLabels[item.kind] || 'Pièce jointe',
        created_at: item.received_at, evidence: item,
      })),
    ]
    const openingItem: ThreadItem[] = openingAt
      ? [{ id: 'opening', sender: 'assistant', text: opening, created_at: openingAt }]
      : []
    return [...openingItem, ...items.sort((a, b) => a.created_at.localeCompare(b.created_at))]
  }, [assistantMessages, opening, openingAt, summary?.evidence])

  useEffect(() => { bottom.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' }) }, [thread.length, proposal, busy])

  function addAssistant(text: string) {
    setAssistantMessages(current => [...current, {
      id: crypto.randomUUID(), role: 'assistant', text, created_at: new Date().toISOString(),
    }])
  }

  function addUser(text: string) {
    setAssistantMessages(current => [...current, {
      id: crypto.randomUUID(), role: 'insured', text, created_at: new Date().toISOString(),
    }])
  }

  function confirmSummary() {
    if (!summary || phase !== 'confirm') return
    addUser('Oui, ces informations sont exactes.')
    const next = nextQuestion(summary)
    if (next) {
      setPhase('collect')
      setFallbackField(next.field)
      addAssistant(`J’ai besoin de quelques informations supplémentaires. ${next.text}`)
    } else {
      setPhase('photos')
      addAssistant(photoRequest)
    }
  }

  function correctSummary() {
    if (busy) return
    addUser('Je souhaite corriger une information.')
    setPhase('correct')
    addAssistant('D’accord. Quelle information souhaitez-vous corriger ?')
  }

  function advance(updated: Summary) {
    setFallback(false)
    const next = nextQuestion(updated)
    if (next) {
      setPhase('collect')
      setFallbackField(next.field)
      addAssistant(`Correction enregistrée. ${next.text}`)
    } else {
      setPhase(phase === 'complete' ? 'complete' : 'photos')
      addAssistant('Correction enregistrée. Vous pouvez ajouter vos photos ou envoyer un message.')
    }
  }

  async function uploadBatch(batch: QueuedFile[]) {
    if (!summary || uploadLock.current) return
    uploadLock.current = true
    setBusy('upload')
    setError('')
    let received = 0
    try {
      for (const entry of batch) {
        let pending = entry.pending
        setFiles(current => current.map(item => item.id === entry.id ? { ...item, status: 'uploading', error: undefined } : item))
        try {
          if (!supabase) throw new Error('Le dépôt de fichiers n’est pas configuré.')
          let current = await guestRequest<Summary>('/v1/deposit/summary', session)
          if (!pending || (!pending.stored && new Date(pending.intent.expires_at).getTime() < Date.now())) {
            const sha = await sha256(entry.file)
            const kind = kindFor(entry.file)
            const intent = await guestRequest<UploadIntent>('/v1/deposit/evidence/upload-intents', session, {
              method: 'POST', body: JSON.stringify({ filename: entry.file.name, mime_type: entry.file.type,
                byte_size: entry.file.size, client_sha256: sha, kind, expected_state_version: current.state_version }),
            })
            pending = { intent, sha, kind, file: entry.file }
          }
          if (!pending.stored) {
            const { error: uploadError } = await supabase.storage.from(pending.intent.bucket).uploadToSignedUrl(
              pending.intent.storage_path, pending.intent.token, pending.file,
              { contentType: pending.file.type, upsert: false })
            if (uploadError && !uploadError.message.toLowerCase().includes('already exists')) throw uploadError
            pending.stored = true
          }
          // Finalization uses the latest version, including previously finalized files in this batch.
          current = await guestRequest<Summary>('/v1/deposit/summary', session)
          const updated = await guestRequest<Summary>('/v1/deposit/evidence', session, {
            method: 'POST', body: JSON.stringify({ storage_path: pending.intent.storage_path,
              client_sha256: pending.sha, kind: pending.kind, expected_state_version: current.state_version }),
          })
          setSummary(updated)
          received++
          if (entry.preview) URL.revokeObjectURL(entry.preview)
          setFiles(current => current.filter(item => item.id !== entry.id))
        } catch (err) {
          if (isAccessError(err)) { handleError(err, 'Session expirée.'); break }
          const message = err instanceof Error ? err.message : 'Envoi impossible.'
          setFiles(current => current.map(item => item.id === entry.id ? { ...item, status: 'failed', error: message, pending } : item))
        }
      }
      if (received) setNotice(`${received} pièce${received > 1 ? 's' : ''} reçue${received > 1 ? 's' : ''}. Ajoutez les autres vues utiles, puis terminez l’ajout.`)
    } finally { uploadLock.current = false; setBusy('') }
  }

  async function send(event: FormEvent) {
    event.preventDefault()
    const message = input.trim()
    if (!summary || !session || busy || (!message && !files.some(item => item.status === 'ready')) || phase === 'confirm' || (proposal && message)) return
    setBusy('send')
    setError('')
    setNotice('')
    try {
      if (message) {
        if (summary.conversation_available) {
          const key = messageKey.current ?? crypto.randomUUID()
          messageKey.current = key
          await guestRequest<unknown>('/v1/deposit/conversation', session, {
            method: 'POST', body: JSON.stringify({ client_message_id: key, body: message }),
          })
          messageKey.current = null
        }
        setInput('')
        addUser(message)
        const history = [
          { role: 'assistant', text: opening },
          ...assistantMessages.map(item => ({ role: item.role, text: item.text })),
        ].slice(-12)
        const question = phase === 'collect' ? nextQuestion(summary) : null
        const targetField = question?.field
        try {
          const answer = await guestRequest<ChatResponse>('/v1/deposit/chat', session, {
            method: 'POST', body: JSON.stringify({ message, history, target_field: targetField }),
          })
          if (answer.proposal && (!targetField || answer.proposal.field === targetField)) {
            setProposal(answer.proposal)
            addAssistant(`J’ai noté ${fieldLabels[answer.proposal.field] || answer.proposal.field} : ${proposalLabel(answer.proposal)}. Pouvez-vous confirmer ?`)
          } else {
            addAssistant(targetField
              ? answer.reply || question!.text
              : answer.reply || 'Votre message a été reçu.')
          }
        } catch (err) {
          if (isAccessError(err)) throw err
          setFallback(true)
          addAssistant('Je ne peux pas interpréter votre réponse pour le moment. Vous pouvez réessayer ou utiliser le formulaire de correction ci-dessous.')
        }
      }
      if (files.some(item => item.status === 'ready')) await uploadBatch(files.filter(item => item.status === 'ready'))
    } catch (err) {
      handleError(err, 'Envoi impossible. Réessayez.')
    } finally { setBusy('') }
  }

  async function confirmProposal() {
    if (!summary || !proposal) return
    setBusy('correction')
    setError('')
    try {
      const updated = await guestRequest<Summary>('/v1/deposit/corrections', session, {
        method: 'POST', body: JSON.stringify({
          expected_state_version: summary.state_version, field: proposal.field,
          value: proposal.value, reason: 'Confirmé dans la conversation',
        }),
      })
      setSummary(updated)
      setProposal(null)
      addUser('Oui, je confirme cette information.')
      advance(updated)
    } catch (err) { handleError(err, 'Correction impossible.') }
    finally { setBusy('') }
  }

  async function submitFallback(event: FormEvent) {
    event.preventDefault()
    if (!summary) return
    setBusy('correction')
    setError('')
    try {
      const normalized = fallbackField === 'incident_at' && fallbackValue
        ? new Date(fallbackValue).toISOString() : fallbackValue
      const updated = await guestRequest<Summary>('/v1/deposit/corrections', session, {
        method: 'POST', body: JSON.stringify({
          expected_state_version: summary.state_version, field: fallbackField,
          value: normalized, reason: 'Correction saisie sans assistant',
        }),
      })
      setSummary(updated)
      setFallbackValue('')
      setProposal(null)
      addUser(`${fieldLabels[fallbackField] || fallbackField} : ${displayValue(fallbackField, normalized)}`)
      advance(updated)
    } catch (err) { handleError(err, 'Correction impossible.') }
    finally { setBusy('') }
  }

  async function openEvidence(item: Evidence) {
    try {
      const result = await guestRequest<{ url: string }>(`/v1/deposit/evidence/${item.id}/read-url`, session)
      window.open(result.url, '_blank', 'noopener,noreferrer')
    } catch (err) { handleError(err, 'Pièce indisponible.') }
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files || [])
    const problems: string[] = []
    const queued: QueuedFile[] = []
    for (const file of selected) {
      const problem = validateFile(file)
      if (problem) { problems.push(`${file.name} : ${problem}`); continue }
      queued.push({ id: crypto.randomUUID(), file, status: 'ready',
        preview: file.type.startsWith('image/') && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(file) : undefined })
    }
    setError(problems.join(' '))
    setFiles(current => [...current, ...queued])
    if (phase === 'complete') setPhase('photos')
    event.target.value = ''
  }

  function removeFile(entry: QueuedFile) {
    if (entry.preview) URL.revokeObjectURL(entry.preview)
    setFiles(current => current.filter(item => item.id !== entry.id))
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  return <main className="deposit-app">
    <header className="deposit-header">
      <span className="deposit-mark" aria-hidden="true">C</span>
      <div><strong>Claimroom</strong><small>Conversation privée</small></div>
    </header>
    {busy === 'opening' && <p className="deposit-loading" role="status">Ouverture de votre conversation…</p>}
    {!summary && busy !== 'opening' && <div className="deposit-access" role="alert">
      <h1>Accès à la conversation</h1>
      <p>{error || 'Rouvrez le lien privé reçu par SMS.'}</p>
    </div>}
    {summary && <section className="deposit-shell" aria-label="Conversation du dossier">
      <div className="deposit-thread" role="log" aria-label="Messages de votre dossier">
        {thread.map(item => <article key={item.id} className={`deposit-bubble is-${item.sender}`}>
          <span className="deposit-sender">{item.sender === 'insured' ? 'Vous' : 'Assistant'}</span>
          {item.evidence
            ? <button type="button" className="deposit-file-link" onClick={() => void openEvidence(item.evidence!)}>
              <span aria-hidden="true">📎</span> {item.text}
            </button>
            : <p>{item.text}</p>}
          <time dateTime={item.created_at}>{new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' }).format(new Date(item.created_at))}</time>
        </article>)}
        {summary.analysis_status === 'awaiting_review' && <p className="deposit-notice" role="status">L’analyse est terminée. Votre assureur examine l’estimation.</p>}
        {busy === 'send' && <p className="deposit-typing" role="status">Envoi en cours…</p>}
        <div ref={bottom} />
      </div>
      {phase === 'confirm' && <div className="deposit-confirmation">
        <button type="button" onClick={confirmSummary}>Oui, ces informations sont exactes</button>
        <button type="button" className="deposit-quiet" onClick={correctSummary}>Corriger une information</button>
      </div>}
      {proposal && <div className="deposit-proposal">
        <strong>Confirmer la correction</strong>
        <p>{fieldLabels[proposal.field] || proposal.field} : {proposalLabel(proposal)}</p>
        <div><button type="button" disabled={!!busy} onClick={() => void confirmProposal()}>Confirmer</button>
          <button type="button" className="deposit-quiet" disabled={!!busy} onClick={() => {
            setProposal(null)
            addAssistant(phase === 'collect' && nextQuestion(summary)
              ? `D’accord. ${nextQuestion(summary)!.text}`
              : 'D’accord. Quelle information souhaitez-vous corriger ?')
          }}>Reformuler</button></div>
      </div>}
      {error && <p className="deposit-alert" role="alert">{error}</p>}
      {historySaving && <p className="deposit-notice" role="status">Sauvegarde de la conversation…</p>}
      {historyError && <div className="deposit-alert" role="alert">
        <p>La conversation n’est pas encore sauvegardée. {historyError}</p>
        <button type="button" onClick={() => historyWriter.current?.retry()}>Réessayer la sauvegarde</button>
      </div>}
      {notice && <p className="deposit-notice" role="status">{notice}</p>}
      {phase !== 'confirm' && <>
        <div className="deposit-actions">
          {phase === 'photos' && <button type="button" disabled={!!busy || files.length > 0 || !hasPhotos(summary)} onClick={() => {
            setPhase('complete'); addAssistant(closingMessage); setNotice('')
          }}>Terminer l’ajout</button>}
          {phase === 'photos' && <span>{hasPhotos(summary) ? 'Pensez à la vue d’ensemble et aux gros plans.' : 'Ajoutez une vue d’ensemble et les dommages visibles.'}</span>}
        </div>
        <form className="deposit-composer" onSubmit={event => void send(event)}>
          {files.length > 0 && <ul className="deposit-upload-list" aria-label="Pièces à envoyer">
            {files.map(entry => <li key={entry.id} className="deposit-selected-file">
              {entry.preview ? <img src={entry.preview} alt={`Aperçu de ${entry.file.name}`} /> : <span aria-hidden="true">📎</span>}
              <div><strong>{entry.file.name}</strong><small role="status">{entry.status === 'uploading' ? 'Envoi en cours…' : entry.status === 'failed' ? entry.error : 'Prêt à envoyer'}</small></div>
              {entry.status === 'failed' && <button type="button" className="deposit-file-retry" disabled={!!busy} onClick={() => void uploadBatch([entry])}>Réessayer {entry.file.name}</button>}
              <button type="button" disabled={!!busy} aria-label={`Retirer ${entry.file.name}`} onClick={() => removeFile(entry)}>×</button>
            </li>)}
          </ul>}
          <div className="deposit-compose-row">
            <input ref={fileInput} className="deposit-file-input" type="file" multiple accept={fileTypes} aria-label="Choisir une pièce jointe" onChange={chooseFile} />
            <button type="button" className="deposit-attach" aria-label="Ajouter une pièce jointe"
              title="Ajouter des photos ou des documents" disabled={!!busy}
              onClick={() => fileInput.current?.click()}>+</button>
            <textarea aria-label="Votre message" value={input} maxLength={1000} rows={1}
              placeholder="Un message ou une correction…" onChange={event => { setInput(event.target.value); messageKey.current = null }}
              onKeyDown={onComposerKeyDown} disabled={!!busy || !!proposal} />
            <button type="submit" className="deposit-send" aria-label="Envoyer"
              disabled={!!busy || (!input.trim() && !files.some(item => item.status === 'ready')) || (!!proposal && !!input.trim())}>↑</button>
          </div>
        </form>
      </>}
      {<details className="deposit-fallback" open={fallback || undefined}>
        <summary>Modifier directement une information</summary>
        <form onSubmit={event => void submitFallback(event)}>
          <label>Champ<select value={fallbackField} onChange={event => { setFallbackField(event.target.value); setFallbackValue('') }}>
            {Object.entries(fieldLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select></label>
          <label>Nouvelle valeur{fallbackField === 'incident_at'
            ? <input type="datetime-local" required value={fallbackValue} onChange={event => setFallbackValue(event.target.value)} />
            : fallbackField === 'danger_status' || fallbackField === 'injury_status'
              ? <select required value={fallbackValue} onChange={event => setFallbackValue(event.target.value)}>
                <option value="">Choisir</option><option value="yes">Oui</option><option value="no">Non</option><option value="unknown">Je ne sais pas</option>
              </select>
              : <textarea required maxLength={1000} value={fallbackValue} onChange={event => setFallbackValue(event.target.value)} />}
          </label>
          <button disabled={!!busy}>Enregistrer la correction</button>
        </form>
      </details>}
    </section>}
  </main>
}
