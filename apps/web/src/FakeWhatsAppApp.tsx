import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { supabase } from './lib/supabase'
import './fake-whatsapp.css'

const apiBase = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

type Message = { id: string; side: 'agent' | 'you'; text: string; created_at: string; evidence_id: string | null; filename: string | null }
type Conversation = { case_id: string; number: string; deposit_url: string | null; messages: Message[] }
type Summary = { state_version: number; evidence: { id: string }[] }
type UploadIntent = { bucket: string; storage_path: string; token: string }

const fileKinds = [
  { value: 'scene_photo', label: 'Photo de la scène' },
  { value: 'damage_photo', label: 'Photo des dégâts' },
  { value: 'vehicle_photo', label: 'Photo du véhicule' },
  { value: 'document', label: 'Document PDF' },
  { value: 'scene_video', label: 'Vidéo de la scène' },
] as const

async function guestRequest<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...init, cache: 'no-store', referrerPolicy: 'no-referrer',
    headers: { Authorization: `Bearer ${token}`, ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null
    const code = body?.error?.code
    if (code === 'expired_session' || code === 'expired_grant' || code === 'revoked_grant') throw new Error('La session a expiré. Rouvrez le téléphone depuis le dossier gestionnaire.')
    throw new Error(body?.error?.message || 'Action impossible. Réessayez.')
  }
  return response.json() as Promise<T>
}

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

function linkify(text: string, depositUrl: string | null) {
  const parts = text.split(/(https?:\/\/[^\s]+)/g)
  return parts.map((part, index) => {
    if (!depositUrl || part !== depositUrl) return part
    try {
      const parsed = new URL(part)
      if (parsed.protocol !== 'https:' && parsed.hostname !== 'localhost' && parsed.hostname !== '127.0.0.1') return part
      return <a key={index} href={part} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Ouvrir le lien de dépôt sécurisé</a>
    } catch { return part }
  })
}

export default function FakeWhatsAppApp() {
  const [session, setSession] = useState('')
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [draft, setDraft] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [kind, setKind] = useState<string>('scene_photo')
  const [busy, setBusy] = useState('opening')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pendingEvidenceId, setPendingEvidenceId] = useState<string | null>(null)
  const pendingTextId = useRef<string | null>(null)
  const pendingAttachmentId = useRef<string | null>(null)
  const started = useRef(false)
  const feedEnd = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (started.current) return
    started.current = true
    const token = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('token')
    window.history.replaceState(null, '', '/fake-whatsapp')
    if (!token) { setError('Ouvrez le téléphone depuis un message simulé dans le dossier gestionnaire.'); setBusy(''); return }
    void guestRequest<{ session_token: string }>('/v1/deposit/session', token)
      .then(async result => {
        const current = await guestRequest<Conversation>('/v1/fake-whatsapp/conversation', result.session_token)
        setSession(result.session_token); setConversation(current)
      })
      .catch(reason => setError(reason instanceof Error ? reason.message : 'Impossible d’ouvrir le téléphone.'))
      .finally(() => setBusy(''))
  }, [])

  useEffect(() => {
    if (!session) return
    const timer = window.setInterval(() => {
      void guestRequest<Conversation>('/v1/fake-whatsapp/conversation', session).then(setConversation).catch(() => {})
    }, 4000)
    return () => window.clearInterval(timer)
  }, [session])

  useEffect(() => { feedEnd.current?.scrollIntoView?.({ behavior: 'smooth' }) }, [conversation?.messages.length])

  async function sendText(event: FormEvent) {
    event.preventDefault()
    const body = draft.trim()
    if (!session || !body || busy) return
    setBusy('sending'); setError(''); setNotice('')
    const clientId = pendingTextId.current ?? crypto.randomUUID()
    pendingTextId.current = clientId
    try {
      const result = await guestRequest<Conversation>('/v1/fake-whatsapp/messages', session, {
        method: 'POST', body: JSON.stringify({ client_message_id: clientId, body }),
      })
      setConversation(result); setDraft(''); pendingTextId.current = null
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Message non enregistré.') }
    finally { setBusy('') }
  }

  async function upload(event: FormEvent) {
    event.preventDefault()
    if (!session || !file || !supabase || busy) return
    const compatible = (kind === 'document' && file.type === 'application/pdf') ||
      (kind === 'scene_video' && file.type === 'video/mp4') ||
      (!['document', 'scene_video'].includes(kind) && ['image/jpeg', 'image/png', 'image/webp'].includes(file.type))
    const sizeLimit = kind === 'scene_video' ? 10_000_000 : 5_242_880
    if (!compatible || file.size === 0 || file.size > sizeLimit) {
      setError('Choisissez un fichier correspondant au type indiqué (5 Mo pour les images et PDF, 10 Mo pour la vidéo).'); return
    }
    setBusy('uploading'); setError(''); setNotice('')
    try {
      let evidenceId = pendingEvidenceId
      if (!evidenceId) {
        const current = await guestRequest<Summary>('/v1/deposit/summary', session)
        const digest = await sha256(file)
        const intent = await guestRequest<UploadIntent>('/v1/deposit/evidence/upload-intents', session, {
          method: 'POST', body: JSON.stringify({ filename: file.name, mime_type: file.type, byte_size: file.size,
            client_sha256: digest, kind, expected_state_version: current.state_version }),
        })
        const result = await supabase.storage.from(intent.bucket).uploadToSignedUrl(intent.storage_path, intent.token, file, {
          contentType: file.type, upsert: false,
        })
        if (result.error && !result.error.message.toLowerCase().includes('already exists')) throw result.error
        const updated = await guestRequest<Summary>('/v1/deposit/evidence', session, {
          method: 'POST', body: JSON.stringify({ storage_path: intent.storage_path, client_sha256: digest,
            kind, expected_state_version: current.state_version }),
        })
        evidenceId = updated.evidence.find(item => !current.evidence.some(previous => previous.id === item.id))?.id ?? null
        if (!evidenceId) throw new Error('La pièce a été ajoutée, mais son identifiant manque. Rechargez le dossier.')
        setPendingEvidenceId(evidenceId)
      }
      const clientId = pendingAttachmentId.current ?? crypto.randomUUID()
      pendingAttachmentId.current = clientId
      const updatedConversation = await guestRequest<Conversation>('/v1/fake-whatsapp/messages', session, {
        method: 'POST', body: JSON.stringify({ client_message_id: clientId, body: '', evidence_id: evidenceId }),
      })
      setConversation(updatedConversation); setPendingEvidenceId(null); pendingAttachmentId.current = null; setFile(null)
      setNotice('Fichier ajouté au dossier et à la conversation simulée.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Le fichier n’a pas pu être ajouté.') }
    finally { setBusy('') }
  }

  return <main className="fake-wa-page">
    <header className="fake-wa-top"><a href="/">Claimroom</a><span>TEST · WHATSAPP SIMULÉ</span></header>
    <div className="fake-wa-phone">
      <div className="fake-wa-head"><span className="fake-wa-avatar">C</span><div><strong>Claimroom insurance</strong><small>{conversation?.number || 'Connexion au numéro…'}</small></div></div>
      <p className="fake-wa-warning">Simulation dans le navigateur. Aucun message n’est envoyé à un vrai téléphone.</p>
      <div className="fake-wa-feed" role="log" aria-label="Conversation simulée">
        {busy === 'opening' && <p>Ouverture de la conversation…</p>}
        {conversation?.messages.map(message => <div key={message.id} className={`fake-wa-bubble ${message.side}`}>
          {message.text && <p>{message.side === 'agent' ? linkify(message.text, conversation.deposit_url) : message.text}</p>}
          {message.evidence_id && <p className="fake-wa-attachment">📎 {message.filename || 'Pièce jointe'}</p>}
          <small>{new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' }).format(new Date(message.created_at))}</small>
        </div>)}
        <div ref={feedEnd} />
      </div>
      {error && <p className="fake-wa-error" role="alert">{error}</p>}
      {notice && <p className="fake-wa-notice" role="status">{notice}</p>}
      {conversation && <div className="fake-wa-compose">
        <form onSubmit={event => void sendText(event)}><label className="fake-wa-visually-hidden" htmlFor="fake-wa-text">Votre message</label>
          <input id="fake-wa-text" value={draft} maxLength={1600} placeholder="Écrire un message…" disabled={Boolean(busy)}
            onChange={event => { setDraft(event.target.value); pendingTextId.current = null }} />
          <button disabled={Boolean(busy) || !draft.trim()}>{busy === 'sending' ? 'Envoi…' : 'Envoyer'}</button></form>
        <form onSubmit={event => void upload(event)} className="fake-wa-upload">
          <label>Type de pièce <select value={kind} disabled={Boolean(busy) || Boolean(pendingEvidenceId)} onChange={event => setKind(event.target.value)}>
            {fileKinds.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select></label>
          <label>Fichier <input type="file" accept="image/jpeg,image/png,image/webp,application/pdf,video/mp4"
            disabled={Boolean(busy) || Boolean(pendingEvidenceId)} onChange={event => { setFile(event.target.files?.[0] || null); pendingAttachmentId.current = null }} /></label>
          <button disabled={Boolean(busy) || !file}>{pendingEvidenceId ? 'Réessayer l’envoi dans le chat' : busy === 'uploading' ? 'Ajout…' : 'Joindre au dossier'}</button>
        </form>
      </div>}
    </div>
  </main>
}
