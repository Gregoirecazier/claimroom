import { useEffect, useState } from 'react'
import { listPortalChat, listSmsLinks } from '../lib/api'
import type { CaseView, Evidence } from '../lib/api'
import { readConversation } from '../lib/conversation'

type Message = { id: string; side: 'agent' | 'you'; text: string; createdAt: string; evidenceId?: string }
const time = (value: string) => new Intl.DateTimeFormat('fr-FR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
const kindLabel: Record<string, string> = {
  scene_photo: 'Photo d’ensemble', damage_photo: 'Photo des dégâts', vehicle_photo: 'Photo du véhicule',
  scene_video: 'Vidéo', document: 'Document', other: 'Pièce jointe',
}
const localMessages = (id: string): Message[] => readConversation(id).map(item => ({ ...item, id: String(item.id) }))

export function CaseConversationPanel({ caseView, readUrls, onOpenEvidence, accessToken }: {
  caseView: CaseView
  readUrls: Record<string, string>
  onOpenEvidence: (evidence: Evidence) => void
  accessToken?: string
}) {
  const [messages, setMessages] = useState<Message[]>(() => accessToken ? [] : localMessages(caseView.id))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    let active = true
    setMessages(accessToken ? [] : localMessages(caseView.id))
    setError('')
    if (!accessToken) {
      const refresh = (event: Event) => {
        if ((event as CustomEvent<string>).detail === caseView.id) setMessages(localMessages(caseView.id))
      }
      window.addEventListener('claimroom:conversation-updated', refresh)
      return () => window.removeEventListener('claimroom:conversation-updated', refresh)
    }
    setLoading(true)
    void listSmsLinks(accessToken, caseView.id).then(async links => {
      const unique = [...new Map(links.map(link => [link.id, link])).values()]
      const results = await Promise.allSettled(unique.map(link => listPortalChat(accessToken, caseView.id, link.id)))
      if (!active) return
      const collected = results.flatMap(result => result.status === 'fulfilled' ? result.value : [])
      setMessages([...new Map(collected.map(item => [item.id, {
        id: item.id, side: item.sender === 'insured' ? 'you' as const : 'agent' as const,
        text: item.body, createdAt: item.created_at,
      }])).values()])
      if (results.some(result => result.status === 'rejected')) setError('Certains échanges n’ont pas pu être chargés. Réessayez.')
    }).catch(() => { if (active) setError('L’historique est momentanément indisponible. Réessayez.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [accessToken, caseView.id, caseView.state_version, revision])

  const evidence = [...caseView.evidence].sort((a, b) => a.received_at.localeCompare(b.received_at))
  const byId = new Map(evidence.map(item => [item.id, item]))
  const shown = new Set(messages.map(item => item.evidenceId).filter(Boolean))
  const entries = [
    ...messages.map(message => ({ kind: 'message' as const, at: message.createdAt, id: `message-${message.id}`, message })),
    ...evidence.filter(item => !shown.has(item.id)).map(item => ({ kind: 'evidence' as const, at: item.received_at, id: `evidence-${item.id}`, evidence: item })),
  ].sort((a, b) => a.at.localeCompare(b.at))

  function attachment(item: Evidence) {
    return <button type="button" className="ir-conversation-attachment" onClick={() => onOpenEvidence(item)}
      aria-label={`Ouvrir ${item.original_filename || kindLabel[item.kind] || 'la pièce jointe'}`}>
      {!accessToken && item.mime_type.startsWith('image/') && readUrls[item.id]
        ? <img src={readUrls[item.id]} alt="" />
        : <span className="ir-conversation-file-icon" aria-hidden="true">{item.mime_type === 'application/pdf' ? 'PDF' : item.mime_type.startsWith('video/') ? '▶' : '▧'}</span>}
      <span><strong>{item.original_filename || kindLabel[item.kind] || 'Pièce jointe'}</strong>
        <small>{kindLabel[item.kind] || 'Pièce jointe'}</small></span>
    </button>
  }

  return <section className="ir-card ir-conversation" id="conversation" aria-label="Conversation et documents du dossier">
    <div className="ir-section-heading"><div><h2>{accessToken ? 'Échanges et pièces reçues' : 'Simulation de cette session'}</h2></div>
      {accessToken && <button className="ir-text-button" disabled={loading} onClick={() => setRevision(value => value + 1)}>{loading ? 'Chargement…' : 'Actualiser'}</button>}</div>
    {error && <p role="alert" className="ir-error">{error}</p>}
    <div className="ir-conversation-feed" role="log" aria-label="Messages et documents envoyés">
      {!loading && !error && entries.length === 0 && <p className="ir-conversation-empty">Aucun échange ni pièce reçue pour le moment.</p>}
      {entries.map(entry => entry.kind === 'message'
        ? <div key={entry.id} className={`ir-conversation-bubble ${entry.message.side}`}>
            <small>{entry.message.side === 'agent' ? 'Gestionnaire' : 'Assuré'} · {time(entry.at)}</small>
            <p>{entry.message.text}</p>
            {entry.message.evidenceId && byId.has(entry.message.evidenceId) && attachment(byId.get(entry.message.evidenceId)!)}
          </div>
        : <div key={entry.id} className="ir-conversation-bubble document">
            <small>Pièce reçue · {time(entry.at)}</small>{attachment(entry.evidence)}
          </div>)}
    </div>
  </section>
}
