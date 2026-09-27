import { makeCases } from './fixtures.mjs'
import { exampleJourney, exampleVoice } from './journey.mjs'
import { claimSms } from './claimSms.mjs'
import { readUpload } from './storage.mjs'

// Explicit, in-memory presentation adapter, loaded only by `npm run demo`.
const cases = makeCases().map(c => ({ ...c, voice_session: exampleVoice(c) }))
const waiting = cloneWaitingCase()
cases.push(waiting)
function cloneWaitingCase() {
  const c = structuredClone(cases[0])
  c.id = '00000000-0000-4000-8000-000000000004'; c.scenario_id = 'waiting'; c.status = 'collecting'
  c.intake.insured_reference = 'CLM-2026-0846'; c.intake.insured_name = null; c.intake.incident_at = null
  c.intake.insured_vehicle = 'Peugeot 208'; c.intake.insured_plate = 'DE-215-MO'
  c.intake.missing_fields = ['insured_name', 'incident_at']
  c.evidence = []; c.estimate = null; c.quote = null; c.quote_status = 'no_estimate'; c.current_draft = null
  c.latest_analysis = null; c.gate_results = []; c.voice_session = null
  return c
}
const stateKey = 'claimroom.ui-preview.v1'
let restored
try { restored = JSON.parse(globalThis.localStorage?.getItem(stateKey) || 'null') } catch { /* Fresh preview. */ }
if (restored?.cases?.length === 4) cases.splice(0, cases.length, ...restored.cases)
const networkGarages = [
  { id: 'atelier-republique', name: 'Atelier République', address: '12 rue du Faubourg-du-Temple, Paris', latitude: 48.8687, longitude: 2.3654, distance_m: 720, network_status: 'approved' },
  { id: 'carrosserie-opera', name: 'Carrosserie Opéra', address: '27 rue de Provence, Paris', latitude: 48.8747, longitude: 2.3352, distance_m: 1100, network_status: 'approved' },
  { id: 'garage-marais', name: 'Garage du Marais', address: '8 rue de Bretagne, Paris', latitude: 48.8639, longitude: 2.3629, distance_m: 1450, network_status: 'approved' },
]
for (const c of cases) c.partner_garages = structuredClone(networkGarages)
const journeys = new Map(restored?.journeys || [])
function persist() {
  try { globalThis.localStorage?.setItem(stateKey, JSON.stringify({ cases, journeys: [...journeys] })) } catch { /* Memory-only when storage is unavailable. */ }
}
globalThis.addEventListener?.('storage', event => {
  if (event.key !== stateKey || !event.newValue) return
  try {
    const next = JSON.parse(event.newValue)
    if (next.cases?.length !== 4) return
    cases.splice(0, cases.length, ...next.cases)
    journeys.clear()
    for (const [id, journey] of next.journeys || []) journeys.set(id, journey)
  } catch { /* Retain the last usable preview state. */ }
})

const uploadIntents = new Map()
const garageSettings = new Map()
const clone = value => structuredClone(value)
const find = id => {
  const value = cases.find(c => c.id === id)
  if (!value) throw new Error('Dossier introuvable.')
  return value
}
function editable(id, version) {
  const c = find(id)
  if (c.state_version !== version) throw new Error('Le dossier a changé. Rechargez-le.')
  if (['registered', 'sent'].includes(c.status)) throw new Error('Ce dossier a déjà été transmis.')
  return c
}
function quoteStatus(c) {
  return !c.estimate ? 'no_estimate' : !c.quote ? 'no_quote'
    : c.quote.attached_estimate_version !== c.estimate.version ? 'outdated'
    : c.quote.total_ttc_minor !== c.estimate.total_minor ? 'mismatch' : 'matched'
}
function change(c, event) {
  c.state_version++
  c.content_revision++
  c.status = 'collecting'
  c.approval = null
  c.current_draft = null
  c.quote_status = quoteStatus(c)
  c.report.needs_reanalysis = Boolean(c.latest_analysis)
  c.report.analysis_current = false
  c.gate_results = []
  c.updated_at = new Date().toISOString()
  c.timeline.push({ id: crypto.randomUUID(), event_type: event, occurred_at: c.updated_at, state_version_after: c.state_version })
  persist()
  return clone(c)
}
export const getCurrentUser = async () => ({ id: 'demo', email: 'gestionnaire@example.test' })
export const listCases = async () => clone(cases)
export const listCasesPage = async (_token, { query = '', offset = 0, limit = 25 } = {}) => {
  const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[\s-]+/g, '')
  const q = normalize(query)
  const filtered = cases.filter(c => [c.id, ...Object.values(c.intake)].some(value => normalize(value).includes(q)))
    .sort((a,b) => b.updated_at.localeCompare(a.updated_at) || b.id.localeCompare(a.id))
  return { items: clone(filtered.slice(offset, offset + limit)), total: filtered.length, offset, limit, has_more: offset + limit < filtered.length }
}
export const getCase = async (_token, id) => clone(find(id))
export const deleteCase = async (_token, id) => {
  const c = find(id)
  cases.splice(cases.indexOf(c), 1)
}
export const getEvidenceReadUrl = async (_token, id, evidenceId) => {
  const e = find(id).evidence.find(e => e.id === evidenceId)
  if (!e) throw new Error('Pièce introuvable dans ce dossier.')
  return { evidence_id: e.id, url: (await readUpload(e.storage_path))?.url || e.storage_path, expires_at: new Date(Date.now() + 300000).toISOString() }
}
export const updateIntake = async (_token, id, version, patch) => {
  const c = editable(id, version)
  Object.assign(c.intake, patch)
  c.intake.missing_fields = ['insured_name', 'incident_at', 'location', 'narrative'].filter(field => !c.intake[field])
  if ('insured_name' in patch || 'insured_vehicle' in patch || 'insured_plate' in patch) c.intake.insured_identity_source = 'handler_entered'
  c.report.lines[0].text = c.intake.narrative
  return change(c, 'case.intake_updated')
}
export const editReportLine = async (_token, id, lineId, text, uncertainty, version) => {
  const c = editable(id, version)
  const line = c.report.lines.find(l => l.id === lineId)
  if (!line || !text.trim()) throw new Error('Correction invalide.')
  Object.assign(line, { text, uncertainty, claim_kind: 'handler_edit', signed_by: 'Gestionnaire démo', signed_at: new Date().toISOString() })
  return change(c, 'case.report_line_edited')
}
export const upsertEstimate = async (_token, id, version, items, total) => {
  const c = editable(id, version)
  if (!items.length || !Number.isSafeInteger(total) || total <= 0 || items.some(i => !i.label.trim() || !Number.isSafeInteger(i.amount_minor) || i.amount_minor < 0)
      || items.reduce((sum, item) => sum + item.amount_minor, 0) !== total) throw new Error('Estimation invalide.')
  c.estimate = { id: crypto.randomUUID(), version: (c.estimate?.version || 0) + 1, line_items: clone(items), total_minor: total, currency: 'EUR', tax_basis: 'TTC', estimate_source: 'handler' }
  return change(c, 'case.estimate_updated')
}
export const attachQuote = async (_token, id, version, evidenceId, amount) => {
  const c = editable(id, version)
  const e = c.evidence.find(e => e.id === evidenceId && e.kind === 'document' && e.mime_type === 'application/pdf')
  if (!e || !Number.isSafeInteger(amount) || amount <= 0) throw new Error('Sélectionnez le PDF du dossier et son total TTC.')
  if (c.quote?.evidence_id === evidenceId && c.quote.total_ttc_minor === amount && c.quote.attached_estimate_version === (c.estimate?.version ?? null)) return clone(c)
  c.quote = { id: crypto.randomUUID(), version: (c.quote?.version || 0) + 1, evidence_id: e.id, filename: e.original_filename,
    total_ttc_minor: amount, amount_source: 'handler_entered', attached_estimate_version: c.estimate?.version ?? null }
  return change(c, 'case.quote_attached')
}
export const removeQuote = async (_token, id, version) => {
  const c = editable(id, version)
  c.quote = null
  return change(c, 'case.quote_removed')
}
export const runAnalysis = async (_token, id, version) => {
  const c = editable(id, version)
  const ready = c.scenario_id === 'g1' && quoteStatus(c) === 'matched'
  c.latest_analysis = { id: crypto.randomUUID(), status: 'ready', mode: 'mock', input_content_revision: c.content_revision, output: null }
  c.gate_results = [
    { gate: 'intake', status: 'passed', reason_codes: [], source_refs: [] },
    { gate: 'counterparty', status: c.scenario_id === 'g1' ? 'passed' : 'blocked', reason_codes: c.scenario_id === 'g1' ? [] : ['ambiguous_vehicle_or_coverage'], source_refs: [] },
    { gate: 'evidence', status: ready ? 'passed' : 'blocked', reason_codes: ready ? [] : [c.scenario_id === 'g3' ? 'supported_proposition_required' : `quote_${quoteStatus(c)}`], source_refs: [] },
    { gate: 'approval', status: ready ? 'needs_review' : 'blocked', reason_codes: [ready ? 'handler_approval_required' : 'upstream_gates_not_passed'], source_refs: [] },
  ]
  c.current_draft = ready ? { ...makeCases()[0].current_draft, id: crypto.randomUUID(), sha256: crypto.randomUUID(),
    content_revision: c.content_revision, amount_minor: c.estimate.total_minor,
    package: { schema_version: 1, report_lines: clone(c.report.lines), estimate: clone(c.estimate), quote: clone(c.quote), video_analyses: [] },
    body: `Madame, Monsieur,\n\nDossier de démonstration ${c.intake.insured_reference}. Montant proposé : ${(c.estimate.total_minor / 100).toFixed(2)} € TTC.\n\nAucun assureur réel ne sera contacté.` } : null
  c.status = ready ? 'review_ready' : 'collecting'
  c.approval = null
  c.report.analysis_current = true
  c.report.needs_reanalysis = false
  c.state_version++
  return { case: clone(c), analysis_run: clone(c.latest_analysis) }
}
export const updateCurrentDraft = async (_token, id, version, patch) => {
  const c = editable(id, version)
  if (!c.current_draft) throw new Error('Relancez l’analyse.')
  Object.assign(c.current_draft, clone(patch), { id: crypto.randomUUID(), sha256: crypto.randomUUID(), version: c.current_draft.version + 1 })
  c.approval = null
  c.status = 'review_ready'
  c.state_version++
  return clone(c)
}
export const approveDraft = async (_token, id, draftId, draftSha, version) => {
  const c = editable(id, version)
  const d = c.current_draft
  if (!d || d.id !== draftId || d.sha256 !== draftSha || d.content_revision !== c.content_revision || quoteStatus(c) !== 'matched'
      || d.amount_minor !== c.estimate.total_minor || !d.attachment_ids.includes(c.quote.evidence_id)
      || ['intake', 'counterparty', 'evidence'].some(name => !c.gate_results.some(g => g.gate === name && g.status === 'passed'))) throw new Error('Le dossier doit être vérifié avant validation.')
  c.approval = { id: crypto.randomUUID(), draft_id: d.id, draft_sha256: d.sha256, approved_content_revision: c.content_revision, superseded_at: null }
  c.status = 'approved'
  c.state_version++
  return clone(c)
}
const receipts = new Map()
function transmit(id, version, kind, key) {
  const c = find(id)
  const receiptKey = `${id}:${kind}:${key}`
  if (receipts.has(receiptKey)) return clone(receipts.get(receiptKey))
  if (c.state_version !== version || !c.approval || !c.current_draft
      || c.approval.draft_id !== c.current_draft.id || c.approval.draft_sha256 !== c.current_draft.sha256
      || c.approval.approved_content_revision !== c.content_revision
      || c.status !== (kind === 'registration' ? 'approved' : 'registered')) throw new Error('Validation humaine actuelle requise.')
  const action = { id: crypto.randomUUID(), kind, mode: 'mock', status: 'confirmed', created_at: new Date().toISOString(), reference: `DEMO-${kind === 'registration' ? 'REG' : 'SEND'}-${c.intake.insured_reference}` }
  if (kind === 'send') action.envelope = { ...transmissionPreview(c), send_action_id: action.id, send_reference: action.reference, sent_at: action.created_at }
  c.actions.push(action)
  c.status = kind === 'registration' ? 'registered' : 'sent'
  c.state_version++
  c.timeline.push({ id: crypto.randomUUID(), event_type: `case.${kind}_simulated`, occurred_at: action.created_at, state_version_after: c.state_version })
  receipts.set(receiptKey, action)
  return clone(action)
}
export const simulateRegistration = async (_token, id, version, key) => transmit(id, version, 'registration', key)
export const simulateSend = async (_token, id, version, key) => transmit(id, version, 'send', key)

// Network-dependent workflows stay explicit; no fabricated agent outputs.
const unavailable = async () => { throw new Error('Cette action nécessite l’application connectée à l’API. L’aperçu ne lance aucun agent et ne transfère aucun fichier.') }
export const createEvidenceUploadIntent = async (_token, id, body) => {
  editable(id, body.expected_state_version)
  const path = `local-upload/${id}/${crypto.randomUUID()}/${body.filename}`
  uploadIntents.set(path, { ...body, caseId: id })
  return { bucket: 'local-preview', storage_path: path, token: 'local-upload-only',
    mime_type: body.mime_type, byte_size: body.byte_size, expires_at: new Date(Date.now() + 3600000).toISOString() }
}
export const finalizeEvidence = async (_token, id, path, sha, kind, version) => {
  const c = editable(id, version)
  const intent = uploadIntents.get(path)
  if (!intent || intent.caseId !== id || !(await readUpload(path))) throw new Error('Transfert local introuvable.')
  if (c.evidence.some(e => e.storage_path === path)) return clone(c)
  c.evidence.push({ id: crypto.randomUUID(), case_id: id, kind, storage_path: path, source_kind: 'local_preview_upload',
    mode: 'mock', mime_type: intent.mime_type, byte_size: intent.byte_size, client_sha256: sha, checksum_status: 'client_declared',
    sha256_verified: null, role: null, display_order: c.evidence.length, original_filename: intent.filename, received_at: new Date().toISOString() })
  return change(c, 'case.evidence_added')
}
export const runVision = unavailable
export const runVideoAnalysis = unavailable
export const getVoiceRecordingReadUrl = unavailable
export const reextractVoiceCall = unavailable
export const seedG1Media = unavailable
export const receiveCctv = unavailable

// Keep the presentation adapter aligned with the current UI contract. Network actions
// deliberately fail here; only the connected app can run agents or collect new files.
export class ApiError extends Error {
  constructor(status, error) { super(error?.message || 'Action indisponible'); this.status = status; this.code = error?.code; this.details = error?.details || {} }
}
export const autoFakeSms = async () => { throw new ApiError(503, { code: 'fake_whatsapp_not_configured', message: 'Téléphone simulé indisponible dans cet aperçu.' }) }
export const listSmsLinks = async (_token, id) => [{ id: `preview-sms-${id}`, status: 'delivered', created_at: find(id).created_at, mode: 'mock', recipient_masked: '+33 6 •• •• •• 00' }]
export const getSmsLink = async (_token, id) => ({ url: `/depot#token=demo-${find(id).scenario_id}` })
export const createVoiceWebTestSession = unavailable
export const getVoiceWebTestCall = unavailable
export const getSmsRecipientCandidate = unavailable
export const previewSms = unavailable
export const createSms = unavailable
export const getSmsDepositLink = unavailable
export const listSms = async () => []
export const searchCameras = async () => ({ status: 'unavailable', mode: 'mock', source_version: 'local-preview', candidates: [], reason: 'La recherche de caméras nécessite l’application connectée.' })
export const createCameraRequest = unavailable
export const approveCameraRequest = unavailable
export const updateCameraRequestStatus = unavailable

function transmissionPreview(c) {
  const d = c.current_draft
  if (!d) throw new Error('Proposition indisponible.')
  const registration = c.actions.find(a => a.kind === 'registration' && a.status === 'confirmed')
  return clone({ schema_version: 1, mode: 'mock', simulation: 'Aperçu local sans transmission externe',
    case_id: c.id, draft_id: d.id, draft_sha256: d.sha256, content_revision: c.content_revision,
    approval_id: c.approval?.id || null, registration_action_id: registration?.id || null,
    registration_reference: registration?.reference || null, recipient: d.recipient,
    body: d.body, transmission_comment: d.transmission_comment || '', amount_minor: d.amount_minor,
    currency: d.currency, package: d.package,
    attachments: c.evidence.filter(e => d.attachment_ids.includes(e.id)).map(e => ({
      id: e.id, kind: e.kind, mime_type: e.mime_type, filename: e.original_filename, byte_size: e.byte_size,
      checksum: e.sha256_verified || e.client_sha256 || '', checksum_status: e.checksum_status,
    })),
  })
}
export const getTransmissionPreview = async (_token, id) => transmissionPreview(find(id))
export const downloadTransmissionReceipt = async (_token, id) => {
  const receipt = find(id).actions.find(a => a.kind === 'send' && a.status === 'confirmed')?.envelope
  if (!receipt) throw new Error('Aucun reçu d’envoi disponible.')
  const url = URL.createObjectURL(new Blob([JSON.stringify(receipt, null, 2)], { type: 'application/json' }))
  const a = document.createElement('a'); a.href = url; a.download = 'recu-demo.json'; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const runMediaAnalysis = unavailable
export const listWhatsAppInbound = async () => []
export const getMediaWorkflow = async (_token, id) => ({ content_revision: find(id).content_revision,
  workflow: null, correspondence: [], email_configured: false, worker_configured: false })
export const advanceMediaWorkflow = async (token, id) => getMediaWorkflow(token, id)
export const retryMediaWorkflow = async (token, id) => { journeys.delete(id); return getMediaWorkflow(token, id) }
export const createCameraMail = unavailable
export const editCorrespondence = unavailable
export const sendCorrespondence = unavailable
export const mapCameras = async (_token, id, radiusM = 150) => ({
  mode: 'mock', status: 'available', address: find(id).intake.location, resolved_address: null,
  latitude: null, longitude: null, radius_m: radiusM, reason: null,
  cameras: [{ id: 'demo-camera-1', latitude: 0, longitude: 0, distance_m: 24,
    label: 'Caméra de démonstration', operator: null, camera_type: null, source_url: null }],
})
export const getPhotoVideoMatch = async (_token, id, photoId) => {
  const c = find(id)
  if (!c.evidence.some(item => item.id === photoId && item.mime_type.startsWith('image/')))
    throw new Error('Photo absente du dossier.')
  if (c.scenario_id === 'g1') return { status: 'matched', photo_id: photoId,
    plate: 'FR482KL', video_id: 'g1-piece-2' }
  return { status: 'pending', photo_id: photoId, plate: null, video_id: null }
}

// Astra and insurer approval require the connected API, including in local previews.
export const getAccidentJourney = async (_token, id) => {
  const c = find(id)
  let journey = journeys.get(id)
  if (!journey || journey.content_revision !== c.content_revision) {
    journey = exampleJourney(c)
    journeys.set(id, journey)
    if (journey.review?.status === 'awaiting_review' && c.status === 'collecting') c.status = 'review_ready'
    persist()
  }
  return clone(journey)
}
export const approveAccident = async (token, id, body) => {
  const c = find(id)
  const journey = await getAccidentJourney(token, id)
  if (body.expected_content_revision !== c.content_revision || body.review_id !== journey.review.id)
    throw new Error('Le dossier a changé. Rechargez la proposition.')
  if (journey.review.status !== 'awaiting_review') throw new Error('Complétez les éléments manquants avant de valider.')
  if (!Number.isSafeInteger(body.amount_minor) || body.amount_minor <= 0) throw new Error('Montant invalide.')
  if (body.amount_minor !== 525000 && !body.amendment_reason?.trim()) throw new Error('Précisez le motif de modification.')
  journey.review.status = 'approved'
  journey.review.approved_amount_minor = body.amount_minor
  journey.review.amendment_reason = body.amendment_reason
  journey.notifications = journey.notification_preview.map((n, i) => ({ ...n, id: `preview-notification-${i}`, status: 'simulated', error_code: null,
    body: n.channel === 'sms' ? claimSms(c, journey.review.assessment, body.amount_minor) : n.body }))
  c.status = 'sent'; c.state_version++; c.updated_at = new Date().toISOString()
  journeys.set(id, journey)
  persist()
  return clone(journey)
}
export const accidentVideo = async (_token, id, videoId) => {
  const c = find(id)
  const video = c.evidence.find(e => e.id === videoId && e.mime_type.startsWith('video/'))
  if (!video) throw new Error('Vidéo absente de cet exemple.')
  return (await readUpload(video.storage_path))?.url || video.storage_path
}


export const getGarageSms = async (_token, id) => clone(garageSettings.get(id) || {
  mode: 'mock', trigger: 'photos', location: find(id).intake.location,
  settings: { recipient: '+33600000000', enabled: false, origin_json: null, location_text: find(id).intake.location }, jobs: [],
})
export const configureGarageSms = async (token, id, _version, recipient, enabled, origin) => {
  const state = await getGarageSms(token, id)
  state.settings = { recipient, enabled, origin_json: origin, location_text: find(id).intake.location }
  garageSettings.set(id, state)
  return clone(state)
}
export const previewGarages = async (_token, id) => ({
  status: 'ready', origin: { latitude: 48.87, longitude: 2.33 }, radius_m: 500,
  garages: [{ osm_id: 'fictional-garage', name: 'Garage des Ateliers · exemple fictif', distance_m: 320,
    address: 'Rue des Ateliers-Démo, Paris', latitude: 48.87, longitude: 2.3344, directions_url: '#' }],
  location_candidates: [], sms_body: 'Exemple fictif : le Garage des Ateliers se trouve à 320 m du lieu déclaré. Aucun SMS réel envoyé.',
  attribution: 'Données de présentation préparées manuellement', attribution_url: '#',
})
export const queueGarageSms = async (token, id) => {
  const state = await getGarageSms(token, id)
  state.jobs = [{ id: crypto.randomUUID(), mode: 'mock', status: 'done', delivery_status: null, error_code: null,
    sms_body: 'Exemple local : suggestion de garage préparée. Aucun envoi.', created_at: new Date().toISOString() }]
  garageSettings.set(id, state)
  return clone(state)
}

export const listPortalChat = async (_token, id) => {
  const c = find(id)
  return [
    { id: `intro-${id}`, sender: 'manager', body: 'Votre déclaration est enregistrée. Vous pouvez vérifier vos informations et ajouter vos photos dans le chat privé.', created_at: c.created_at },
    ...(c.evidence.length ? [{ id: `photos-${id}`, sender: 'insured', body: 'Voici les photos du véhicule et des dommages.', created_at: c.updated_at }] : []),
  ]
}

// Detailed estimate editing and the connected send endpoint need the API.
// The prepared journey records simulated notifications during approveAccident.
export const editAccidentEstimate = unavailable
export const sendAccidentNotifications = unavailable
