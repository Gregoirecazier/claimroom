import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent, MouseEvent } from 'react'
import type { Session } from '@supabase/supabase-js'
import { authConfigured, supabase } from './lib/supabase'
import { caseIdFromPath } from './lib/caseList'
import { displayLabel as label } from './lib/presentation'
import { EvidencePreview } from './components/EvidencePreview'
import { VoiceIntakePanel } from './components/VoiceIntakePanel'
import { FollowUpSmsPanel } from './components/FollowUpSmsPanel'
import { SmsDepositPanel } from './components/SmsDepositPanel'
import { CaseConversationPanel } from './components/CaseConversationPanel'
import { CaseIcon } from './components/CaseOverview'
import { nextCaseAction, sectionFromHash } from './lib/caseJourney'
import { AccidentJourneyPanel } from './components/AccidentJourneyPanel'
import GaragePanel from './features/garages/GaragePanel'
import { PhotoLookupPanel } from './components/PhotoLookupPanel'
import { useAppHistory } from './lib/useAppHistory'
import logoUrl from '../public/favicon.png'
import {
  createEvidenceUploadIntent, deleteCase, finalizeEvidence, getCase,
  getCurrentUser, getEvidenceReadUrl, listCasesPage, updateIntake,
} from './lib/api'
import type { CaseSummary, CaseView, Evidence, EvidenceKind, EvidenceReadUrl, IntakePatch } from './lib/api'

const date = (value: string | null) => value ? new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : 'À confirmer'
const localDateTime = (value: string | null | undefined) => {
  if (!value) return ''
  const parsed = new Date(value)
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}
const missingFieldLabel: Record<string, string> = { insured_name: 'nom et prénom', incident_at: 'date et heure', location: 'lieu', narrative: 'récit', insured_vehicle: 'véhicule', insured_plate: 'immatriculation', danger_status: 'danger', injury_status: 'blessures' }
const caseTitle = (item: CaseSummary) => item.intake.insured_reference || `Dossier ${item.id.slice(0, 8)}`

type PendingUpload = { caseId: string; path: string; sha: string; kind: EvidenceKind; expiresAt: string }

export default function G1App() {
  const [session, setSession] = useState<Session | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [cases, setCases] = useState<CaseSummary[]>([])
  const [listLoading, setListLoading] = useState(true)
  const [caseTotal, setCaseTotal] = useState(0)
  const [hasMoreCases, setHasMoreCases] = useState(false)
  const [selected, setSelected] = useState<CaseView | null>(null)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [query, setQuery] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [readUrls, setReadUrls] = useState<Record<string, string>>({})
  const [preview, setPreview] = useState<{ evidence: Evidence; signed: EvidenceReadUrl; caseId: string } | null>(null)
  const [editing, setEditing] = useState(false)
  const [intake, setIntake] = useState<IntakePatch & { insured_name?: string | null }>({})
  const [uploadKind, setUploadKind] = useState<EvidenceKind>('damage_photo')
  const [pendingUpload, setPendingUpload] = useState<PendingUpload | null>(null)
  const [section, setSection] = useState(() => sectionFromHash(window.location.hash))
  const history = useAppHistory()
  const [scrollRevision, setScrollRevision] = useState(0)
  const [contentMinHeight, setContentMinHeight] = useState<number>()
  const caseHeader = useRef<HTMLDivElement>(null)
  const detailSections = useRef<HTMLDivElement>(null)
  const navigationRequest = useRef(0)
  const activeToken = useRef<string | null>(null)
  const listRequest = useRef(0)
  const previousQuery = useRef('')
  const deletionInProgress = useRef(false)

  useEffect(() => {
    if (!supabase) { setAuthLoading(false); return }
    void supabase.auth.getSession().then(({ data }) => { setSession(data.session); setAuthLoading(false) })
    const { data: listener } = supabase.auth.onAuthStateChange((_event, next) => setSession(next))
    return () => listener.subscription.unsubscribe()
  }, [])

  useEffect(() => {
    navigationRequest.current += 1
    activeToken.current = session?.access_token ?? null
    setCases([])
    setListLoading(Boolean(session))
    setSelected(null)
    setPreview(null)
    if (!session) return
    void loadCases(session.access_token)
  }, [session?.access_token])

  useEffect(() => {
    if (!session || query === previousQuery.current) return
    previousQuery.current = query
    const request = ++listRequest.current
    setListLoading(true)
    const timer = setTimeout(() => {
      void listCasesPage(session.access_token, { query, offset: 0, limit: 20 }).then(page => {
        if (request !== listRequest.current || activeToken.current !== session.access_token) return
        setCases(page.items); setCaseTotal(page.total); setHasMoreCases(page.has_more)
      }).catch(error => { if (request === listRequest.current && activeToken.current === session.access_token) setNotice(error instanceof Error ? error.message : 'Recherche indisponible.') })
        .finally(() => { if (request === listRequest.current && activeToken.current === session.access_token) setListLoading(false) })
    }, 250)
    return () => clearTimeout(timer)
  }, [query, session?.access_token])

  useEffect(() => {
    if (!session || !selected) { setReadUrls({}); return }
    let cancelled = false
    setReadUrls({})
    void Promise.all(selected.evidence.filter(item => item.mime_type.startsWith('image/')).map(async item => {
      try {
        const result = await getEvidenceReadUrl(session.access_token, selected.id, item.id)
        return [item.id, result.url] as const
      } catch { return null }
    })).then(items => { if (!cancelled) setReadUrls(Object.fromEntries(items.filter(item => item !== null) as [string, string][])) })
    return () => { cancelled = true }
  }, [session?.access_token, selected?.id, selected?.content_revision])

  async function loadCases(token: string) {
    const request = navigationRequest.current
    const listGeneration = listRequest.current
    try {
      await getCurrentUser(token)
      const result = await listCasesPage(token, { query: previousQuery.current, offset: 0, limit: 20 })
      if (activeToken.current !== token) return
      if (listGeneration === listRequest.current) { setCases(result.items); setCaseTotal(result.total); setHasMoreCases(result.has_more); setListLoading(false) }
      const pathId = caseIdFromPath(window.location.pathname)
      if (pathId && request === navigationRequest.current) await openCase(pathId, token, false)
    } catch (error) { if (activeToken.current === token) { setNotice(error instanceof Error ? error.message : 'Impossible de charger les dossiers.'); setListLoading(false) } }
  }

  async function loadMoreCases() {
    if (!session || !hasMoreCases || busy) return
    const token = session.access_token
    const queryAtStart = query
    const generation = listRequest.current
    setBusy('more-cases')
    try {
      const page = await listCasesPage(token, { query: queryAtStart, offset: cases.length, limit: 20 })
      if (generation !== listRequest.current || activeToken.current !== token) return
      setCases(current => [...current, ...page.items.filter(item => !current.some(existing => existing.id === item.id))])
      setCaseTotal(page.total); setHasMoreCases(page.has_more)
    } catch (error) { if (generation === listRequest.current && activeToken.current === token) setNotice(error instanceof Error ? error.message : 'Impossible de charger la suite.') }
    finally { setBusy('') }
  }

  function applyCase(view: CaseView) {
    setSelected(view)
    setCases(current => current.map(item => item.id === view.id ? view : item))
    setIntake({
      reported_at: view.intake.reported_at,
      insured_reference: view.intake.insured_reference,
      insured_name: view.intake.insured_name,
      insured_vehicle: view.intake.insured_vehicle,
      insured_plate: view.intake.insured_plate,
      policy_reference: view.intake.policy_reference,
      incident_at: view.intake.incident_at,
      location: view.intake.location,
      vehicle_country: view.intake.vehicle_country,
      narrative: view.intake.narrative,
      danger_status: view.intake.danger_status,
      injury_status: view.intake.injury_status,
    })
  }

  async function openCase(id: string, token = session?.access_token, navigate = true) {
    if (!token || activeToken.current !== token) return
    const request = ++navigationRequest.current
    setBusy('open'); setNotice('')
    try {
      const view = await getCase(token, id)
      if (request !== navigationRequest.current || activeToken.current !== token) return
      applyCase(view)
      if (navigate) { const target = nextCaseAction(view).target; history.push(`/cases/${id}#${target}`); setSection(sectionFromHash(`#${target}`)) }
      else if (window.location.hash) setSection(sectionFromHash(window.location.hash))
      else setSection(sectionFromHash(`#${nextCaseAction(view).target}`))
      setEditing(false)
    } catch (error) {
      if (request === navigationRequest.current && activeToken.current === token) {
        setSelected(null)
        setNotice(error instanceof Error ? error.message : 'Impossible d’ouvrir ce dossier.')
      }
    } finally { if (request === navigationRequest.current) setBusy('') }
  }

  useEffect(() => {
    const onPop = () => {
      const id = caseIdFromPath(window.location.pathname)
      if (id && id === selected?.id) { syncSection(); return }
      if (id) void openCase(id, session?.access_token, false)
      else { navigationRequest.current += 1; setSelected(null); setPreview(null); setBusy('') }
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [session?.access_token, selected?.id])

  function showList() { navigationRequest.current += 1; setSelected(null); setPreview(null); setBusy(''); history.push('/'); window.scrollTo({ top: 0, behavior: 'instant' }) }

  async function removeCase(item: CaseSummary) {
    if (!session || deletionInProgress.current || busy || listLoading) return
    if (!window.confirm(`Supprimer le dossier « ${caseTitle(item)} » (${item.id.slice(0, 8)}) ?\n\nLe dossier et son historique seront supprimés de l’application. Cette action est irréversible.`)) return
    const token = session.access_token
    deletionInProgress.current = true
    setDeletingId(item.id); setNotice('')
    try {
      await deleteCase(token, item.id)
      if (activeToken.current !== token) return
      setCases(current => current.filter(entry => entry.id !== item.id))
      setCaseTotal(current => Math.max(0, current - 1))
      setNotice(`Le dossier « ${caseTitle(item)} » a été supprimé.`)
    } catch (error) {
      if (activeToken.current === token) setNotice(`Impossible de supprimer le dossier. ${error instanceof Error ? error.message : 'Veuillez réessayer.'}`)
    } finally {
      deletionInProgress.current = false
      setDeletingId(null)
    }
  }

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!supabase) return
    setBusy('sign-in'); setNotice('')
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) setNotice(error.message)
    setBusy('')
  }

  async function saveIntake(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!session || !selected) return
    setBusy('intake'); setNotice('')
    try {
      const patch: IntakePatch & { insured_name?: string | null } = {
        ...intake,
        reported_at: selected.intake.reported_at,
        incident_at: intake.incident_at || null,
        insured_reference: intake.insured_reference?.trim() || null,
        insured_name: intake.insured_name?.trim() || null,
        insured_vehicle: intake.insured_vehicle?.trim() || null,
        insured_plate: intake.insured_plate?.trim() || null,
        location: intake.location?.trim() || null,
        narrative: intake.narrative?.trim() || '',
      }
      applyCase(await updateIntake(session.access_token, selected.id, selected.state_version, patch))
      setEditing(false); setNotice(selected.status === 'collecting' ? 'Déclaration enregistrée.' : 'Déclaration enregistrée. Les validations antérieures sont à reprendre.')
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Impossible d’enregistrer la déclaration.') }
    finally { setBusy('') }
  }

  async function uploadEvidence(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (!files.length || !session || !selected || !supabase) return
    setBusy('upload'); setNotice(`Ajout de ${files.length} pièce${files.length > 1 ? 's' : ''}…`)
    let pending: PendingUpload | null = null
    let current = selected
    let completed = 0
    try {
      for (const file of files) {
        const isVideo = ['scene_video', 'cctv_video', 'insured_video'].includes(uploadKind)
        if (isVideo ? !['video/mp4', 'video/quicktime', 'video/webm'].includes(file.type) || file.size < 1 || file.size > 52_428_800
          : !['image/jpeg', 'image/png', 'image/webp', 'application/pdf'].includes(file.type) || file.size < 1 || file.size > 5_242_880)
          throw new Error(`${file.name} : format ou taille non accepté${isVideo ? ' (vidéo MP4, MOV ou WebM · 50 Mio)' : ' (image ou PDF · 5 Mio)'}.`)
        const hash = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
        const sha = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')
        const intent = await createEvidenceUploadIntent(session.access_token, current.id, {
          filename: file.name, mime_type: file.type, byte_size: file.size, client_sha256: sha,
          kind: uploadKind, expected_state_version: current.state_version,
        })
        const { error } = await supabase.storage.from(intent.bucket).uploadToSignedUrl(intent.storage_path, intent.token, file, { contentType: file.type, upsert: false })
        if (error) throw new Error(error.message)
        pending = { caseId: current.id, path: intent.storage_path, sha, kind: uploadKind, expiresAt: intent.expires_at }
        setPendingUpload(pending)
        current = await finalizeEvidence(session.access_token, current.id, intent.storage_path, sha, uploadKind, current.state_version)
        applyCase(current)
        completed++
        pending = null
        setPendingUpload(null)
        setNotice(`${completed} pièce${completed > 1 ? 's' : ''} sur ${files.length} enregistrée${completed > 1 ? 's' : ''}…`)
      }
      setNotice(`${completed} pièce${completed > 1 ? 's' : ''} ajoutée${completed > 1 ? 's' : ''} au dossier. L’analyse automatique démarre.`)
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'erreur inconnue'
      setNotice(pending ? `${completed} pièce(s) enregistrée(s). Fichier transféré, enregistrement à reprendre : ${detail}` : `${completed} pièce(s) enregistrée(s). ${detail}`)
    } finally { setBusy('') }
  }

  async function retryFinalize() {
    if (!session || !selected || !pendingUpload || pendingUpload.caseId !== selected.id) return
    if (Date.parse(pendingUpload.expiresAt) <= Date.now()) { setPendingUpload(null); setNotice('Le transfert a expiré. Sélectionnez de nouveau le fichier.'); return }
    setBusy('finalize')
    try {
      const latest = await getCase(session.access_token, selected.id)
      const updated = await finalizeEvidence(session.access_token, selected.id, pendingUpload.path, pendingUpload.sha, pendingUpload.kind, latest.state_version)
      applyCase(updated)
      setPendingUpload(null); setNotice('Pièce enregistrée. L’analyse automatique démarre.')
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Enregistrement encore indisponible.') }
    finally { setBusy('') }
  }

  async function openEvidence(item: Evidence) {
    if (!session || !selected) return
    try {
      const signed = await getEvidenceReadUrl(session.access_token, selected.id, item.id)
      if (item.mime_type === 'application/pdf') window.open(signed.url, '_blank', 'noopener,noreferrer')
      else setPreview({ evidence: item, signed, caseId: selected.id })
    } catch { setNotice('Lecture privée indisponible. Réessayez de charger la pièce.') }
  }

  function syncSection() {
    // Reserve the outgoing panel's height until the scroll finishes, so switching
    // from a long section to a short one cannot clamp the document scroll abruptly.
    setContentMinHeight(detailSections.current?.getBoundingClientRect().height)
    setSection(sectionFromHash(window.location.hash))
    setScrollRevision(value => value + 1)
  }

  function navigateSection(event: MouseEvent<HTMLDivElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    const anchor = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href^="#"]') : null
    if (!anchor) return
    event.preventDefault()
    history.push(`${window.location.pathname}${window.location.search}${anchor.hash}`)
    syncSection()
  }

  useEffect(() => {
    window.addEventListener('hashchange', syncSection)
    return () => window.removeEventListener('hashchange', syncSection)
  }, [])

  useEffect(() => {
    if (!selected) return
    let timeout: ReturnType<typeof setTimeout>
    let destination = 0
    const finish = () => setContentMinHeight(undefined)
    const onScrollEnd = () => { if (Math.abs(window.scrollY - destination) < 3) finish() }
    const frame = requestAnimationFrame(() => {
      const hash = window.location.hash
      const target = document.getElementById(hash.slice(1)) || document.getElementById(section)
      if (target instanceof HTMLDetailsElement) target.open = true
      if (hash && hash !== '#overview' && target) {
        const offset = (caseHeader.current?.offsetHeight || 0) + 18
        destination = Math.max(0, target.getBoundingClientRect().top + window.scrollY - offset)
      }
      const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      window.addEventListener('scrollend', onScrollEnd)
      window.scrollTo({ top: destination, behavior: reducedMotion ? 'instant' : 'smooth' })
      if (reducedMotion || Math.abs(window.scrollY - destination) < 3) finish()
      // Also release the reserve if the user interrupts native smooth scrolling.
      timeout = setTimeout(finish, 1200)
    })
    return () => { cancelAnimationFrame(frame); clearTimeout(timeout); window.removeEventListener('scrollend', onScrollEnd) }
  }, [section, scrollRevision, selected?.id])



  const filtered = cases
  const evidenceOrder = (a: Evidence, b: Evidence) => (a.display_order ?? 1000) - (b.display_order ?? 1000) || a.received_at.localeCompare(b.received_at)
  const images = selected?.evidence.filter(item => item.mime_type.startsWith('image/')).sort(evidenceOrder) || []
  const videos = selected?.evidence.filter(item => item.mime_type.startsWith('video/')).sort(evidenceOrder) || []
  const missingFields = selected ? Array.from(new Set([...selected.intake.missing_fields,
    ...(!selected.intake.insured_name?.trim() ? ["insured_name"] : [])])) : []
  const nextAction = selected ? nextCaseAction(selected) : null

  return <div className="ir-app ir-refined" onClick={navigateSection}>
    <a className="ir-skip" href="#main">Aller au contenu</a>
    <aside className="ir-sidebar">
      <a className="ir-brand" href="/" onClick={event => { event.preventDefault(); showList() }}><span className="cr-brand-mark"><img src={logoUrl} alt="" /></span><span>Claimroom</span></a>
      <nav aria-label="Navigation principale">
        <a className={!selected ? 'active' : ''} href="/" onClick={event => { event.preventDefault(); showList() }}><CaseIcon name="folder" />Tous les dossiers<span className="ir-count">{caseTotal}</span></a>

      </nav>
      <div className="ir-sidebar-bottom">{session && <><div className="ir-profile"><span>{session.user.email?.slice(0, 2).toUpperCase()}</span><div><strong>Gestionnaire</strong><small>{session.user.email}</small></div></div><button className="ir-text-button ir-signout" onClick={() => void supabase?.auth.signOut()}>Se déconnecter</button></>}</div>
    </aside>
    <div className="ir-workspace">
      <main className="ir-main" id="main">
        {!authConfigured ? <section className="ir-card ir-auth"><p className="ir-eyebrow">CONFIGURATION</p><h1>Connecter l’espace assureur</h1><p>Renseignez VITE_SUPABASE_URL et VITE_SUPABASE_PUBLISHABLE_KEY dans apps/web/.env.local pour utiliser les dossiers privés.</p></section>
        : authLoading ? <section className="ir-card ir-auth">Chargement de la session…</section>
        : !session ? <section className="ir-card ir-auth"><p className="ir-eyebrow">ACCÈS GESTIONNAIRE</p><h1>Connexion à Claimroom</h1><form onSubmit={event => void signIn(event)}><label className="ir-field">Email<input type="email" required value={email} onChange={event => setEmail(event.target.value)} /></label><label className="ir-field">Mot de passe<input type="password" required value={password} onChange={event => setPassword(event.target.value)} /></label><button className="ir-button ir-primary" disabled={Boolean(busy)}>Se connecter</button></form></section>
        : !selected && (busy === 'open' || (listLoading && caseIdFromPath(window.location.pathname))) ? <section className="ir-card ir-auth" role="status">Ouverture du dossier…</section>
        : !selected ? <>
          <div className="ir-heading"><h1>Tous les dossiers</h1><label className="ir-list-search"><input aria-label="Rechercher un dossier" type="search" disabled={Boolean(deletingId)} value={query} onChange={event => setQuery(event.target.value)} placeholder="Nom, référence ou plaque" /></label></div>
          <section className="ir-card ir-case-list">{listLoading ? <p className="ir-list-empty" role="status">Chargement des dossiers…</p> : <div className="ir-case-table-wrap"><table className="ir-case-table"><thead><tr><th>Dossier</th><th>Statut</th><th>Date</th><th>Actions</th></tr></thead><tbody>{filtered.map(item => <tr key={item.id}>
            <td><a className="ir-case-reference" href={`/cases/${item.id}`} aria-disabled={Boolean(deletingId)} onClick={event => { if (deletingId) { event.preventDefault(); return } if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); void openCase(item.id) } }}>{caseTitle(item)}</a><small className="ir-table-sub">{[item.intake.insured_name, item.intake.insured_vehicle, item.intake.insured_plate].filter(Boolean).join(' · ') || item.intake.location || 'Lieu à confirmer'}</small></td>
            <td data-label="Statut"><span className="ir-status" data-status={item.status}><i aria-hidden="true" />{label(item.status)}</span></td><td data-label="Date">{date(item.created_at)}</td>
            <td data-label="Actions"><button type="button" className="ir-button ir-delete-case" aria-label={`Supprimer le dossier ${caseTitle(item)} (${item.id.slice(0, 8)})`} disabled={Boolean(busy || deletingId)} onClick={() => void removeCase(item)}>{deletingId === item.id ? 'Suppression…' : 'Supprimer'}</button></td>
          </tr>)}</tbody></table>{filtered.length === 0 && <p className="ir-list-empty">{query ? 'Aucun dossier ne correspond à votre recherche.' : 'Aucun dossier disponible.'}</p>}</div>}{!listLoading && hasMoreCases && <button className="ir-button ir-secondary cr-load-more" disabled={Boolean(busy || deletingId)} onClick={() => void loadMoreCases()}>Afficher d’autres dossiers</button>}</section>
        </>
        : <>
          <div className="cr-sticky-case-header" ref={caseHeader}>
            <div className="ir-heading cr-case-heading"><div className="cr-case-identity"><h1>{selected.intake.insured_vehicle || caseTitle(selected)}</h1><div className="cr-case-badges"><span className="ir-status" data-status={selected.status}><i aria-hidden="true" />{label(selected.status)}</span><span className="cr-badge">{selected.intake.insured_plate || 'Plaque à confirmer'}</span></div></div>
              <section className="cr-next-action" aria-label="Prochaine action">{nextAction?.target === 'report' && section === 'report' ? <button className="ir-button ir-primary" onClick={() => setEditing(true)}>Compléter la déclaration →</button> : <a className="ir-button ir-primary" href={nextAction?.target === 'evidence' && section === 'evidence' ? '#upload' : nextAction?.target === 'analysis' && section === 'analysis' ? selected.status === 'review_ready' ? '#review' : '#notifications' : `#${nextAction?.target}`}>{nextAction?.target === 'analysis' && section === 'analysis' ? selected.status === 'review_ready' ? 'Vérifier le montant' : 'Voir le suivi' : nextAction?.label} →</a>}</section>
            </div>
            <nav className="ir-stage-nav" aria-label="Étapes du dossier">{[
              { title: 'Déclaration', target: 'report', done: missingFields.length === 0 },
              { title: 'Pièces', target: 'evidence', done: selected.evidence.some(item => item.mime_type.startsWith('image/')) },
              { title: 'Analyse et décision', target: 'analysis', done: ['approved', 'sent'].includes(selected.status) },
            ].map((step, index) => <a key={step.target} href={`#${step.target}`} aria-current={section === step.target ? 'step' : undefined} className={`${step.done ? 'done' : ''} ${section === step.target ? 'active' : ''}`}><span aria-hidden="true">{step.done ? <CaseIcon name="check" /> : String(index + 1).padStart(2, '0')}</span><strong>{step.title}</strong></a>)}</nav>
          </div>
          <div className="cr-detail-sections" ref={detailSections} style={{ minHeight: contentMinHeight }}>
            <div className="cr-section-stack" hidden={section !== 'report'}>
              <section className="ir-card cr-report" id="report">
                <div className="ir-section-heading"><div><p className="ir-eyebrow">DÉCLARATION</p><h2>Les faits connus</h2></div><button className="ir-text-button" onClick={() => setEditing(!editing)}>{editing ? 'Annuler' : 'Modifier les informations'}</button></div>
                {missingFields.length > 0 && <p className="cr-missing">À compléter : {missingFields.map(field => missingFieldLabel[field] || field).join(', ')}. <button className="ir-text-button" onClick={() => setEditing(true)}>Renseigner maintenant</button></p>}
                {editing ? <form onSubmit={event => void saveIntake(event)} className="ir-intake-form cr-intake-grid">
                  <label className="ir-field">Nom et prénom<input value={intake.insured_name || ''} onChange={event => setIntake({ ...intake, insured_name: event.target.value })} /></label>
                  <label className="ir-field">Véhicule assuré<input value={intake.insured_vehicle || ''} onChange={event => setIntake({ ...intake, insured_vehicle: event.target.value })} /></label>
                  <label className="ir-field">Immatriculation<input value={intake.insured_plate || ''} onChange={event => setIntake({ ...intake, insured_plate: event.target.value })} /></label>
                  <label className="ir-field">Date et heure de l’accident<input type="datetime-local" value={localDateTime(intake.incident_at)} onChange={event => setIntake({ ...intake, incident_at: event.target.value ? new Date(event.target.value).toISOString() : null })} /></label>
                  <label className="ir-field">Lieu<input value={intake.location || ''} onChange={event => setIntake({ ...intake, location: event.target.value })} /></label>
                  <label className="ir-field">Référence du dossier<input value={intake.insured_reference || ''} onChange={event => setIntake({ ...intake, insured_reference: event.target.value })} /></label>
                  <label className="ir-field">Danger immédiat<select value={intake.danger_status || ''} onChange={event => setIntake({ ...intake, danger_status: event.target.value ? event.target.value as IntakePatch['danger_status'] : null })}><option value="">À confirmer</option><option value="no">Non signalé</option><option value="yes">Oui</option><option value="unknown">Inconnu</option></select></label>
                  <label className="ir-field">Blessures<select value={intake.injury_status || ''} onChange={event => setIntake({ ...intake, injury_status: event.target.value ? event.target.value as IntakePatch['injury_status'] : null })}><option value="">À confirmer</option><option value="no">Aucune signalée</option><option value="yes">Oui</option><option value="unknown">Inconnu</option></select></label>
                  <label className="ir-field cr-span-all">Récit<textarea rows={4} value={intake.narrative || ''} onChange={event => setIntake({ ...intake, narrative: event.target.value })} /></label>
                  <button className="ir-button ir-primary" disabled={Boolean(busy)}>Enregistrer la déclaration</button>
                </form> : <>
                  <dl className="cr-facts-grid"><div><dt>Assuré</dt><dd>{selected.intake.insured_name || 'À confirmer'}</dd></div><div><dt>Véhicule</dt><dd>{[selected.intake.insured_vehicle, selected.intake.insured_plate].filter(Boolean).join(' · ') || 'À confirmer'}</dd></div><div><dt>Accident</dt><dd>{date(selected.intake.incident_at)}</dd></div><div><dt>Lieu</dt><dd>{selected.intake.location || 'À confirmer'}</dd></div></dl>
                  <p className="cr-narrative">{selected.intake.narrative || 'Récit à recueillir.'}</p><small className="ir-source-tag">Déclaration de l’assuré · à vérifier</small>
                </>}
              </section>
              <details className="cr-disclosure"><summary>Appel, transcription et extraits de source</summary><VoiceIntakePanel key={`voice-${selected.id}`} caseView={selected} accessToken={session.access_token} onUpdated={() => openCase(selected.id, session.access_token, false)} /></details>
              <details className="cr-disclosure"><summary>Historique des échanges</summary><CaseConversationPanel caseView={selected} accessToken={session.access_token} readUrls={readUrls} onOpenEvidence={item => void openEvidence(item)} /></details>
              <SmsDepositPanel key={`sms-link-${selected.id}`} caseView={selected} accessToken={session.access_token} />
              {selected.voice_session?.mode === 'live' && selected.voice_session.telephony_provider === 'twilio' &&
                ['complete', 'incomplete'].includes(selected.voice_session.status) &&
                <FollowUpSmsPanel key={`follow-up-${selected.id}`} caseView={selected} accessToken={session.access_token} onUpdated={() => openCase(selected.id, session.access_token, false)} />}
            </div>
            <div className="cr-section-stack" hidden={section !== 'evidence'}>
              <section className="ir-card" id="evidence"><div className="ir-section-heading"><span className="ir-heading-icon">▧</span><div><h2>Pièces du dossier <span className="ir-inline-count">{selected.evidence.length}</span></h2></div></div>{images.length === 0 && <p className="cr-empty-evidence">Aucune photo reçue. Ajoutez une vue du véhicule et des photos rapprochées des dégâts pour lancer l’analyse.</p>}<div className="ir-photo-grid">{images.map((item, index) => <button className="ir-photo-card" key={item.id} onClick={() => void openEvidence(item)}><div>{readUrls[item.id] ? <img src={readUrls[item.id]} alt={`Pièce ${index + 1}`} /> : <span className="ir-media-placeholder">Image privée</span>}<span className="ir-photo-id">{item.source_kind.startsWith('synthetic_g') ? `${selected.scenario_id.toUpperCase()}-${index + 1}` : 'PIÈCE'}</span></div><strong>{label(item.role || item.kind)}</strong><small>{item.source_kind.startsWith('synthetic_g') ? 'Image de démonstration' : 'Pièce ajoutée au dossier'}</small></button>)}</div>{videos.map(item => <div className="ir-video-row" key={item.id}><span className="ir-video-icon">▶</span><div><strong>{item.original_filename || 'Vidéo du dossier'}</strong><p>{label(item.kind)}</p></div><button className="ir-button ir-secondary" onClick={() => void openEvidence(item)}>Voir la vidéo</button></div>)}{selected.evidence.filter(item => item.mime_type === 'application/pdf').map(item => <div className="ir-video-row" key={item.id}><span className="ir-video-icon">PDF</span><div><strong>Document joint</strong><p>Document du dossier</p></div><button className="ir-button ir-secondary" onClick={() => void openEvidence(item)}>Ouvrir</button></div>)}<div className="ir-upload-row" id="upload"><label className="ir-field">Type de pièce<select value={uploadKind} onChange={event => setUploadKind(event.target.value as EvidenceKind)}><option value="document">Document</option><option value="scene_photo">Photo de scène</option><option value="vehicle_photo">Photo du véhicule</option><option value="damage_photo">Photo des dégâts</option><option value="other">Autre</option><option value="scene_video">Vidéo de scène</option><option value="cctv_video">Vidéo CCTV</option><option value="insured_video">Vidéo de l’assuré</option></select></label><label className="ir-button ir-secondary ir-file-button">{['scene_video', 'cctv_video', 'insured_video'].includes(uploadKind) ? 'Ajouter une vidéo (50 Mio)' : 'Ajouter image ou PDF'}<input type="file" multiple accept={['scene_video', 'cctv_video', 'insured_video'].includes(uploadKind) ? 'video/mp4,video/quicktime,video/webm' : 'image/jpeg,image/png,image/webp,application/pdf'} disabled={Boolean(busy) || Boolean(pendingUpload)} onChange={event => void uploadEvidence(event)} /></label></div>{pendingUpload?.caseId === selected.id && <button className="ir-button ir-primary" disabled={Boolean(busy)} onClick={() => void retryFinalize()}>Terminer l’enregistrement du fichier transféré</button>}</section>
              <details className="cr-disclosure" id="cameras"><summary>Rechercher des caméras à proximité</summary><PhotoLookupPanel key={`lookup-${selected.id}-${selected.content_revision}`} caseId={selected.id} address={selected.intake.location} accessToken={session.access_token} photos={images} videos={videos} onOpenEvidence={item => void openEvidence(item)} /></details>

            </div>
            <AccidentJourneyPanel key={`journey-${selected.id}`} caseView={selected} accessToken={session.access_token}
              hidden={section === 'report' || section === 'evidence'} pauseUpdates={editing || !!busy}
              garageHelp={<GaragePanel key={`${selected.id}:${selected.intake.location}`} caseView={selected} accessToken={session.access_token} />}
              onUpdated={applyCase} onOpenEvidence={item => void openEvidence(item)} />
          </div>
        </>}
        {notice && <div className={`ir-notice ${notice.includes('Impossible') || notice.includes('reprendre') ? 'ir-error' : ''}`} role="status"><span>{notice}</span><button className="ir-icon-button" aria-label="Fermer" onClick={() => setNotice('')}>×</button></div>}
      </main></div>
    {preview && session && <EvidencePreview key={preview.evidence.id} evidence={preview.evidence} initial={preview.signed}
      renew={() => getEvidenceReadUrl(session.access_token, preview.caseId, preview.evidence.id)} onClose={() => setPreview(null)} />}
  </div>
}
