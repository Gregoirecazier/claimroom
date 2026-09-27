import { listCases, getCase, updateIntake, createEvidenceUploadIntent, finalizeEvidence, getEvidenceReadUrl, getAccidentJourney } from './api.mjs'

export class GuestRequestError extends Error {
  constructor(code, message) { super(message); this.code = code }
}

async function resolve(token) {
  const cases = await listCases()
  const id = token.replace(/^local-deposit-/, '')
  const c = cases.find(item => item.id === id || `demo-${item.scenario_id}` === token)
  if (!c) throw new GuestRequestError('invalid_session', 'Rouvrez la conversation depuis le dossier de démonstration.')
  return c
}

async function summary(c) {
  const journey = await getAccidentJourney('', c.id)
  return { case_id: c.id, state_version: c.state_version, content_revision: c.content_revision,
    source_label: 'Exemple local · aucun message réel', intake: c.intake,
    missing_fields: c.intake.missing_fields, transcript_available: !!c.voice_session,
    recording_status: 'unavailable', evidence: c.evidence, conversation_available: false,
    analysis_status: journey.review?.status || null, analysis_requests: journey.review?.blockers || [],
  }
}

// Mirrors the guest API in the local preview. No HTTP calls or real messages.
export async function guestRequest(path, token, options = {}) {
  const c = await resolve(token)
  const body = options.body ? JSON.parse(options.body) : {}
  if (path === '/v1/deposit/session') return { session_token: `local-deposit-${c.id}` }
  if (path === '/v1/deposit/summary') return summary(await getCase('', c.id))
  if (path === '/v1/deposit/conversation') return []
  if (path === '/v1/deposit/chat') {
    // Explicitly use the direct editor in preview instead of pretending to run an AI.
    throw new GuestRequestError('chat_unavailable', 'L’aperçu local utilise le formulaire de correction, sans assistant connecté.')
  }
  if (path === '/v1/deposit/corrections') {
    const updated = await updateIntake('', c.id, body.expected_state_version, { [body.field]: body.value })
    return summary(updated)
  }
  if (path === '/v1/deposit/evidence/upload-intents') return createEvidenceUploadIntent('', c.id, body)
  if (path === '/v1/deposit/evidence') {
    const updated = await finalizeEvidence('', c.id, body.storage_path, body.client_sha256, body.kind, body.expected_state_version)
    return summary(updated)
  }
  const match = path.match(/^\/v1\/deposit\/evidence\/([^/]+)\/read-url$/)
  if (match) return getEvidenceReadUrl('', c.id, match[1])
  throw new GuestRequestError('preview_unavailable', 'Cette action n’est pas disponible dans l’aperçu local.')
}
