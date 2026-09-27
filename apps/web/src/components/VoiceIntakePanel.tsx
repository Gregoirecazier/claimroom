import { useRef, useState } from 'react'
import { getVoiceRecordingReadUrl, reextractVoiceCall, type CaseView } from '../lib/api'

type VoiceSession = NonNullable<CaseView['voice_session']>
type VoiceFact = VoiceSession['facts'][number]

const statusLabel: Record<VoiceSession['status'], string> = {
  urgent_human_handoff: 'Urgence · intervention humaine',
  collecting: 'Appel en cours',
  complete: 'Déclaration vocale complète',
  incomplete: 'Appel interrompu · à reprendre',
  error: 'Appel échoué · à reprendre',
}

const requiredFields = [
  { field: 'narrative', label: 'Ce qui s’est passé' },
  { field: 'location', label: 'Lieu de l’accident' },
  { field: 'insured_name', label: 'Nom et prénom' },
] as const

const additionalFields = [
  { field: 'stationary', label: 'Véhicule à l’arrêt' },
  { field: 'insured_reference', label: 'Référence du dossier' },
  { field: 'policy_reference', label: 'Référence du contrat' },
  { field: 'insured_plate', label: 'Immatriculation du véhicule assuré' },
  { field: 'insured_vehicle', label: 'Véhicule assuré' },
  { field: 'vehicle_color', label: 'Couleur du véhicule' },
  { field: 'damage_description', label: 'Dégâts décrits' },
] as const

function segmentTime(milliseconds: number | null): string | null {
  if (milliseconds === null) return null
  const seconds = Math.floor(milliseconds / 1000)
  return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`
}

function factValue(fact: VoiceFact): string {
  const choices: Record<string, Record<string, string>> = {
    third_party_involved: { yes: 'Oui', no: 'Non', unknown: 'L’appelant ne sait pas' },
    third_party_presence: { present: 'Resté sur place', left: 'Parti / en fuite', unknown: 'L’appelant ne sait pas' },
    danger_status: { yes: 'Danger signalé', no: 'Pas de danger signalé', unknown: 'L’appelant ne sait pas' },
    injury_severity: { none: 'Aucune blessure signalée', minor: 'Blessures légères signalées', serious: 'Blessures graves signalées', unknown: 'L’appelant ne sait pas' },
    stationary: { yes: 'À l’arrêt', no: 'En mouvement', unknown: 'L’appelant ne sait pas' },
    incident_time: { just_now: 'Juste avant l’appel · heure estimée', unknown: 'L’appelant ne connaît pas l’heure' },
    location: { unknown: 'L’appelant ne connaît pas le lieu' },
  }
  if (choices[fact.field]?.[fact.value]) return choices[fact.field][fact.value]
  if (fact.field === 'incident_time' && fact.value.startsWith('minutes_ago:')) {
    const minutes = Number(fact.value.slice('minutes_ago:'.length))
    return `Environ ${minutes} minute${minutes > 1 ? 's' : ''} avant cette phrase`
  }
  if (fact.field === 'incident_time') {
    const parsed = new Date(fact.value)
    if (!Number.isNaN(parsed.getTime())) return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'short' }).format(parsed)
  }
  return fact.value
}

function narrativeTitle(value: string): string {
  // Keep the caller's wording and qualifiers; the complete quote stays below.
  const sentence = value.replace(/\s+/g, ' ').trim().split(/(?<=[.!?])\s+/u)
    .find(part => /\b(?:accident|collision|choc|heurt\w*|percut\w*|embouti\w*|cartonn\w*)\b|rentr\w*.*dedans/iu.test(part))
  if (!sentence) return 'Récit de l’accident'
  const title = sentence
    .replace(/^(?:(?:oui|bonjour|bonsoir|alors|en fait|eh bien)[,!]\s*)+/iu, '')
    .replace(/^à\s+\d{1,2}(?:h(?:\d{2})?|:\d{2})(?:\s+aujourd['’]hui)?,\s*/iu, '')
    .replace(/^(?:je viens d['’]avoir|j['’]ai (?:eu|subi))\s+(?:un|une)\s+/iu, '')
    .replace(/[.!]+$/u, '')
  const capitalized = title.charAt(0).toLocaleUpperCase('fr-FR') + title.slice(1)
  if (capitalized.length <= 96) return capitalized
  return `${capitalized.slice(0, 93).replace(/\s+\S*$/, '').trimEnd()}…`
}

export function VoiceIntakeChecklist({ session, compact = false, pendingLabel = 'À recueillir pendant l’appel' }: {
  session: VoiceSession | null; compact?: boolean; pendingLabel?: string
}) {
  const facts = new Map(session?.facts.map(fact => [fact.field, fact]) ?? [])
  const capturedCount = requiredFields.filter(item => {
    const fact = facts.get(item.field)
    return fact && fact.uncertainty !== 'uncertain' && !session?.missing_p0.includes(item.field) && (item.field !== 'insured_name' || fact.value.trim().split(/\s+/).length >= 2)
  }).length

  return <div className={`ir-voice-section ir-voice-checklist ${compact ? 'is-compact' : ''}`}>
    <div className="ir-voice-section-head"><div><h4>{compact ? 'Informations recueillies' : session ? 'Ce que l’appel a permis de recueillir' : 'Informations à recueillir'}</h4></div><span>{capturedCount} / {requiredFields.length} renseignées</span></div>
    <div className="ir-voice-table-wrap"><table><thead><tr><th scope="col">Information demandée</th><th scope="col">Extrait de l’appel</th><th scope="col">État</th></tr></thead><tbody>{[...requiredFields, ...additionalFields.filter(item => facts.has(item.field))].map(item => {
      const fact = facts.get(item.field)
      const missing = !fact || fact.uncertainty === 'uncertain' || session?.missing_p0.includes(item.field) || (item.field === 'insured_name' && fact.value.trim().split(/\s+/).length < 2)
      return <tr key={item.field}>
        <th scope="row"><strong>{item.label}</strong></th>
        <td>{fact ? <>
          {fact.field === 'narrative'
            ? <strong className="ir-voice-value ir-voice-narrative-title">{narrativeTitle(fact.value)}</strong>
            : <span className="ir-voice-value">{factValue(fact)}</span>}
          <small className="ir-voice-excerpt">« {fact.excerpt} »</small>
        </> : <span className="ir-voice-placeholder">{session ? 'Non extrait de l’appel' : pendingLabel}</span>}</td>
        <td><span className={`ir-voice-field-status ${missing ? 'is-missing' : 'is-captured'}`}>{missing ? 'À confirmer' : 'Recueilli'}</span></td>
      </tr>
    })}</tbody></table></div>
  </div>
}

export function VoiceIntakePanel({ caseView, accessToken, onUpdated }: { caseView: CaseView; accessToken: string; onUpdated?: () => void | Promise<void> }) {
  const session = caseView.voice_session
  const audioRef = useRef<HTMLAudioElement>(null)
  const audioExpiresAt = useRef(0)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [audioPlaying, setAudioPlaying] = useState(false)
  const [audioLoading, setAudioLoading] = useState(false)
  const [audioError, setAudioError] = useState(false)
  const [reextracting, setReextracting] = useState(false)
  const [reextractError, setReextractError] = useState(false)
  if (!session) return null

  const urgent = session.status === 'urgent_human_handoff'
  const recording = session.recording
  const facts = new Map(session.facts.map(fact => [fact.field, fact]))
  const capturedCount = requiredFields.filter(item => {
    const fact = facts.get(item.field)
    return fact && fact.uncertainty !== 'uncertain' && !session.missing_p0.includes(item.field) && (item.field !== 'insured_name' || fact.value.trim().split(/\s+/).length >= 2)
  }).length
  const missingCount = requiredFields.length - capturedCount
  const callType = session.mode === 'mock' ? 'Appel simulé' : session.telephony_provider === 'web' ? 'Test navigateur' : 'Appel reçu'

  const toggleRecording = async () => {
    const player = audioRef.current
    if (!player) return
    if (!player.paused) { player.pause(); return }
    setAudioError(false)
    setAudioLoading(true)
    try {
      if (!audioUrl || Date.now() >= audioExpiresAt.current - 10_000 || player.error) {
        const signed = await getVoiceRecordingReadUrl(accessToken, caseView.id)
        player.src = signed.url
        audioExpiresAt.current = Date.now() + signed.expires_in * 1000
        setAudioUrl(signed.url)
      }
      await player.play()
    } catch {
      setAudioError(true)
    } finally { setAudioLoading(false) }
  }

  const reextract = async () => {
    setReextracting(true)
    setReextractError(false)
    try {
      await reextractVoiceCall(accessToken, caseView.id)
      await onUpdated?.()
    } catch {
      setReextractError(true)
    } finally {
      setReextracting(false)
    }
  }

  const recordingLabel = recording.status === 'pending' ? 'Enregistrement en attente'
    : recording.status === 'unavailable' ? 'Aucun enregistrement disponible'
      : recording.status === 'error' ? `Échec de récupération de l’enregistrement${recording.error_code ? ` (${recording.error_code})` : ''}`
        : audioPlaying ? 'Mettre l’appel en pause' : 'Écouter l’appel'

  return <section className={`ir-voice-panel ${urgent ? 'ir-voice-urgent' : ''}`} aria-label="État de l’appel">
    <div className="ir-voice-header">
      <div>
        <p className="ir-voice-eyebrow">DÉCLARATION VOCALE <span aria-hidden="true">/</span> {callType}</p>
        <h3>{statusLabel[session.status]}</h3>
        {(urgent || missingCount === 0) && <p className="ir-voice-intro">{urgent ? 'La déclaration nécessite une prise en charge immédiate.' : 'Les informations essentielles de l’appel sont renseignées.'}</p>}
        {session.status === 'incomplete' && session.segments.some(segment => segment.speaker === 'caller') && <button className="ir-button ir-secondary" type="button" disabled={reextracting} onClick={() => void reextract()}>{reextracting ? 'Actualisation en cours…' : 'Actualiser les informations recueillies'}</button>}
        {reextractError && <p role="alert">Actualisation indisponible. Réessayez plus tard.</p>}
      </div>
      <span className={`ir-voice-status ir-voice-status-${session.status}`}>{urgent ? 'Urgent' : missingCount > 0 ? 'À compléter' : 'Complet'}</span>
    </div>

    {urgent && <p className="ir-voice-alert">Arrêter le parcours automatique. Orienter l’appelant vers les secours et un interlocuteur humain.</p>}

    <div className="ir-voice-columns">
    <div className="ir-voice-section ir-voice-conversation">
      <div className="ir-voice-section-head ir-voice-conversation-head"><h4>Échange téléphonique</h4><button type="button" className={`ir-voice-speaker ${audioPlaying ? 'is-playing' : ''}`} aria-label={recordingLabel} title={recordingLabel} aria-pressed={audioPlaying} disabled={recording.status !== 'available' || audioLoading} onClick={() => void toggleRecording()}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M11 5 6.5 9H3v6h3.5L11 19V5Z" /><path d="M15 9a5 5 0 0 1 0 6M18 6a9 9 0 0 1 0 12" /></svg></button></div>
      <audio ref={audioRef} src={audioUrl || undefined} aria-label="Bande son originale de l’appel" preload="none" onPlay={() => setAudioPlaying(true)} onPause={() => setAudioPlaying(false)} onEnded={() => setAudioPlaying(false)} onError={() => setAudioError(true)} />
      {audioError && <p className="ir-voice-audio-error" role="alert">Lecture indisponible. Réessayez.</p>}
      {session.segments.length === 0 ? <p className="ir-voice-empty">Aucune transcription finale disponible pour cet appel.</p> :
        <ol className="ir-voice-turns" aria-label="Transcription de l’appel">{session.segments.map(segment => {
          const caller = segment.speaker === 'caller'
          const start = segmentTime(segment.start_ms)
          return <li key={segment.id} className={`ir-voice-turn ${caller ? 'ir-voice-turn-caller' : 'ir-voice-turn-agent'}`}>
            <div className="ir-voice-turn-meta"><strong>{caller ? 'Appelant' : 'Agent'}</strong>{start && <time>{start}{segment.end_ms === null ? '' : `–${segmentTime(segment.end_ms)}`}</time>}</div>
            <p>{segment.text}</p>
          </li>
        })}</ol>}
    </div>

    <VoiceIntakeChecklist session={session} compact />
    </div>
  </section>
}
