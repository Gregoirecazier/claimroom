import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { Session } from '@supabase/supabase-js'
import type Vapi from '@vapi-ai/web'
import { authConfigured, supabase } from './lib/supabase'
import { createVoiceWebTestSession, getCase, getVoiceWebTestCall, type CaseView, type VoiceWebTestCall } from './lib/api'
import { SimulatedWhatsAppChat } from './components/SimulatedWhatsAppChat'
import { VoiceIntakeChecklist, VoiceIntakePanel } from './components/VoiceIntakePanel'
import { updateVoiceTranscript, type VoiceTurn } from './lib/voiceTranscript'
import './voice-test.css'

type CallStatus = 'preparing' | 'ready' | 'connecting' | 'active' | 'ending' | 'ended' | 'error'

const statusText: Record<CallStatus, string> = {
  preparing: 'Préparation de la session', ready: 'Prêt à démarrer',
  connecting: 'Connexion à l’agent', active: 'Conversation en cours',
  ending: 'Fin de l’appel', ended: 'Conversation terminée', error: 'Connexion indisponible',
}

function errorMessage(error: unknown): string {
  const detail = error instanceof Error ? `${error.name} ${error.message}` : String(error)
  if (/permission|notallowed|microphone|device/i.test(detail)) {
    return 'L’accès au micro a été refusé ou aucun micro n’est disponible. Autorisez le micro puis réessayez.'
  }
  return 'Impossible de lancer la conversation vocale. Réessayez dans un instant.'
}

export default function VoiceTestApp() {
  const [session, setSession] = useState<Session | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState<CallStatus>('preparing')
  const [error, setError] = useState('')
  const [segments, setSegments] = useState<VoiceTurn[]>([])
  const [callId, setCallId] = useState<string | null>(null)
  const [savedCall, setSavedCall] = useState<VoiceWebTestCall | null>(null)
  const [caseView, setCaseView] = useState<CaseView | null>(null)
  const [caseLoadError, setCaseLoadError] = useState(false)
  const [expiresAt, setExpiresAt] = useState(0)
  const [assistantId, setAssistantId] = useState('')
  const client = useRef<Vapi | null>(null)
  const accessToken = useRef('')
  const connectTimer = useRef<number | null>(null)
  const attempt = useRef(0)

  function clearConnectTimer() {
    if (connectTimer.current !== null) window.clearTimeout(connectTimer.current)
    connectTimer.current = null
  }

  useEffect(() => {
    if (!supabase) { setAuthLoading(false); return }
    void supabase.auth.getSession().then(({ data }) => { setSession(data.session); setAuthLoading(false) })
    const { data } = supabase.auth.onAuthStateChange((_event, next) => setSession(next))
    return () => data.subscription.unsubscribe()
  }, [])

  useEffect(() => {
    accessToken.current = session?.access_token || ''
  }, [session?.access_token])

  useEffect(() => {
    if (!session) {
      attempt.current += 1
      clearConnectTimer()
      void client.current?.stop()
      client.current?.removeAllListeners()
      client.current = null
      setStatus('preparing')
      return
    }
    let cancelled = false
    setStatus('preparing')
    setError('')
    void Promise.all([createVoiceWebTestSession(accessToken.current), import('@vapi-ai/web')])
      .then(([grant, module]) => {
        if (cancelled) return
        const vapi = new module.default(grant.token)
        vapi.on('call-start', () => { clearConnectTimer(); setStatus('active') })
        vapi.on('call-end', () => { clearConnectTimer(); setStatus(current => current === 'error' ? current : 'ended') })
        vapi.on('message', message => setSegments(previous => updateVoiceTranscript(previous, message)))
        vapi.on('error', event => { clearConnectTimer(); setError(errorMessage(event)); setStatus('error') })
        client.current = vapi
        setAssistantId(grant.assistant_id)
        setExpiresAt(Date.parse(grant.expires_at))
        setStatus('ready')
      })
      .catch(reason => {
        if (cancelled) return
        setStatus('error')
        setError(reason instanceof Error && /403/.test(reason.message)
          ? 'Cette page est réservée au compte gestionnaire de la démo.'
          : 'La démonstration vocale n’est pas configurée ou est momentanément indisponible.')
      })
    return () => {
      cancelled = true
      attempt.current += 1
      clearConnectTimer()
      const active = client.current
      client.current = null
      active?.removeAllListeners()
      void active?.stop()
    }
  }, [session?.user.id])

  useEffect(() => {
    if (!session || !callId || savedCall?.recording_status === 'available') return
    let cancelled = false
    let attempts = 0
    const check = async () => {
      attempts += 1
      try {
        const current = await getVoiceWebTestCall(accessToken.current, callId)
        if (!cancelled) setSavedCall(current)
      } catch { /* A case appears only after Vapi sends a final transcript. */ }
      if (attempts >= 30) window.clearInterval(timer)
    }
    const timer = window.setInterval(() => { void check() }, 3000)
    void check()
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [session?.user.id, callId, savedCall?.recording_status])

  useEffect(() => {
    if (!session || !savedCall || !callId) return
    let cancelled = false
    let attempts = 0
    let loading = false
    const refresh = async () => {
      if (loading) return
      loading = true
      attempts += 1
      try {
        const view = await getCase(session.access_token, savedCall.case_id)
        if (!cancelled && view.voice_session?.session_id === callId) {
          setCaseView(view)
          setCaseLoadError(false)
        }
      } catch {
        if (!cancelled) setCaseLoadError(true)
      } finally {
        loading = false
        if (attempts >= 30) window.clearInterval(timer)
      }
    }
    const timer = window.setInterval(() => { void refresh() }, 3000)
    void refresh()
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [session?.access_token, savedCall?.case_id, callId])

  async function retryCaseSync() {
    if (!callId) return
    try {
      setSavedCall(await getVoiceWebTestCall(accessToken.current, callId))
      setError('')
    } catch { setError('Le dossier n’est pas encore prêt. Réessayez dans un instant.') }
  }

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!supabase) return
    setError('')
    const result = await supabase.auth.signInWithPassword({ email, password })
    if (result.error) setError('Connexion impossible. Vérifiez le compte gestionnaire.')
  }

  async function start() {
    const activeClient = client.current
    if (!activeClient || !assistantId || status === 'connecting' || status === 'active') return
    if (Date.now() >= expiresAt - 10_000) {
      setError('La session de test a expiré. Renouvelez l’accès pour lancer un nouvel appel.')
      setStatus('error')
      return
    }
    setError('')
    setSegments([])
    setSavedCall(null)
    setCaseView(null)
    setCaseLoadError(false)
    setCallId(null)
    setStatus('connecting')
    const currentAttempt = ++attempt.current
    connectTimer.current = window.setTimeout(() => {
      if (attempt.current !== currentAttempt) return
      attempt.current += 1
      connectTimer.current = null
      setError('Le navigateur n’a pas autorisé le micro. Autorisez-le dans la barre d’adresse, puis réessayez.')
      setStatus('error')
    }, 15_000)
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone unavailable')
      // Ask for permission before Vapi creates a billable call. The SDK opens
      // its own stream after this short preflight; do not retain browser audio.
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach(track => track.stop())
      if (client.current !== activeClient || attempt.current !== currentAttempt) return
      clearConnectTimer()
      connectTimer.current = window.setTimeout(() => {
        if (attempt.current !== currentAttempt) return
        attempt.current += 1
        connectTimer.current = null
        setError('La connexion vocale a expiré. Vérifiez le micro et réessayez.')
        setStatus('error')
        void activeClient.stop()
      }, 20_000)
      const call = await activeClient.start(assistantId)
      if (call?.id && client.current === activeClient && attempt.current === currentAttempt) setCallId(call.id)
    } catch (reason) {
      if (client.current !== activeClient || attempt.current !== currentAttempt) return
      clearConnectTimer()
      setError(errorMessage(reason))
      setStatus('error')
    }
  }

  function stop() {
    if (!client.current || status !== 'active') return
    setStatus('ending')
    void client.current.stop().catch(reason => { setError(errorMessage(reason)); setStatus('error') })
  }

  return <main className="voice-test">
    <header className="voice-test-header"><a href="/">Claimroom</a><span>LAB · TEST VOCAL</span></header>
    <section className="voice-test-shell">
      <div className="voice-test-intro"><p className="voice-test-kicker">PARCOURS S17 · NAVIGATEUR</p>
        <h1>Parler à l’agent vocal</h1>
        <p>Testez la déclaration d’accident avec votre micro, directement ici. L’agent utilisé est celui de la démo téléphonique, avec Vapi et Gradium.</p>
        <div className="voice-test-tags"><span>Sans appel téléphonique</span><span>WhatsApp simulé après l’appel</span><span>Dossier synthétique</span></div>
      </div>
      {!authConfigured ? <section className="voice-test-card"><h2>Configuration requise</h2><p>La connexion gestionnaire Supabase doit être configurée pour lancer un test.</p></section>
        : authLoading ? <section className="voice-test-card"><p>Chargement de la session…</p></section>
          : !session ? <section className="voice-test-card"><h2>Connexion gestionnaire</h2><p>Le test utilise le compte de démonstration pour rattacher les transcriptions à un dossier privé.</p>
            <form onSubmit={event => void signIn(event)} className="voice-test-form">
              <label>Email<input type="email" required value={email} onChange={event => setEmail(event.target.value)} /></label>
              <label>Mot de passe<input type="password" required value={password} onChange={event => setPassword(event.target.value)} /></label>
              <button type="submit">Se connecter</button>
            </form>{error && <p role="alert" className="voice-test-error">{error}</p>}</section>
            : <div className="voice-test-workspace"><section className="voice-test-callbar" aria-label="État de l’appel">
              <div className="voice-test-callbar-main"><span className={`voice-test-call-indicator voice-test-call-indicator-${status}`} aria-hidden="true" /><div><p className="voice-test-label">TEST VOCAL</p><h2>{statusText[status]}</h2><p className="voice-test-hint">{status === 'active' ? 'Échange en direct avec l’agent.' :
                status === 'connecting' ? 'Autorisez le micro si votre navigateur le demande.' :
                  status === 'ended' ? 'La conversation est terminée.' :
                    'Votre navigateur demandera l’accès au micro au démarrage.'}</p></div></div>
              <div className="voice-test-actions">
                {status === 'active' ? <button type="button" className="voice-test-stop" onClick={stop}>Terminer l’appel</button>
                  : <button type="button" onClick={start} disabled={status === 'preparing' || status === 'connecting' || status === 'ending' || !client.current}>
                    {status === 'ended' ? 'Nouvel appel' : 'Démarrer le test vocal'}</button>}
                {status === 'error' && Date.now() >= expiresAt - 10_000 &&
                  <button type="button" className="voice-test-stop" onClick={() => window.location.reload()}>Renouveler l’accès</button>}
              </div>
              {error && <p role="alert" className="voice-test-error">{error}</p>}
              {savedCall && <a className="voice-test-case-link" href={`/cases/${savedCall.case_id}`}>Ouvrir le dossier et sa bande son →</a>}
            </section>
              {status === 'ended' && caseView?.voice_session ? <div className="voice-test-review">
                <VoiceIntakePanel key={caseView.id} caseView={caseView} accessToken={session.access_token} />
              </div> : <div className="voice-test-live-grid"><section className="ir-voice-section voice-test-transcript"><div className="ir-voice-section-head"><div><p className="ir-voice-eyebrow">TRANSCRIPTION EN DIRECT</p><h4>La conversation</h4></div><span>{segments.length} message{segments.length > 1 ? 's' : ''}</span></div>
                {segments.length === 0 ? <p className="voice-test-empty">Les échanges de l’agent et de l’appelant apparaîtront ici.</p>
                  : <ol className="ir-voice-turns" aria-label="Transcription en direct" aria-live="polite">{segments.map((item, index) => <li key={`${index}-${item.role}`} className={`ir-voice-turn ${item.role === 'user' ? 'ir-voice-turn-caller' : 'ir-voice-turn-agent'}`}>
                    <div className="ir-voice-turn-meta"><strong>{item.role === 'user' ? 'Vous' : 'Agent'}</strong></div><p>{item.text}</p></li>)}</ol>}
              </section><VoiceIntakeChecklist session={caseView?.voice_session ?? null}
                pendingLabel={status === 'ended' || status === 'ending' ? 'Synchronisation du dossier en cours' : undefined} /></div>}
              {status === 'ended' && savedCall && <SimulatedWhatsAppChat key={savedCall.case_id} caseId={savedCall.case_id} accessToken={session.access_token} />}
              {status === 'ended' && savedCall && !caseView?.voice_session && <section className="voice-test-card voice-test-waiting"><h2>Compte rendu en préparation</h2><p>{caseLoadError ? 'Le dossier existe, mais sa transcription finale est momentanément indisponible.' : 'La transcription finale et les informations recueillies vont apparaître ici.'}</p><button type="button" onClick={() => void getCase(session.access_token, savedCall.case_id).then(view => { if (view.voice_session?.session_id === callId) { setCaseView(view); setCaseLoadError(false) } }).catch(() => setCaseLoadError(true))}>Actualiser le compte rendu</button></section>}
              {status === 'ended' && !savedCall && callId && <section className="voice-test-card voice-test-waiting"><h2>Préparation de la conversation</h2><p>Le dossier vocal se synchronise. La discussion s’ouvrira ici automatiquement.</p><button type="button" onClick={() => void retryCaseSync()}>Vérifier maintenant</button></section>}
            </div>}
      <p className="voice-test-footnote">L’audio original, la transcription et les réponses du chat sont rattachés au dossier privé de démonstration. La conversation WhatsApp est une simulation dans le navigateur ; aucun numéro de téléphone n’est utilisé.</p>
    </section>
  </main>
}
