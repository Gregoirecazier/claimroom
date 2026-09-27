const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export type CurrentUser = {
  id: string
  email: string | null
}

export type CaseStatus = 'collecting' | 'review_ready' | 'approved' | 'registered' | 'sent'
export type ProviderStatus = 'matched' | 'no_match' | 'ambiguous' | 'unavailable' | 'error'

export type Intake = {
  reported_at: string
  insured_reference: string | null
  insured_name: string | null
  insured_vehicle: string | null
  insured_plate: string | null
  insured_identity_source: 'synthetic_fixture' | 'handler_entered' | null
  policy_reference: string | null
  incident_at: string | null
  time_source: 'caller_statement' | 'inferred_from_call' | 'handler_entered' | 'insured_correction' | null
  location: string | null
  vehicle_country: string | null
  narrative: string
  danger_status: 'yes' | 'no' | 'unknown' | null
  injury_status: 'yes' | 'no' | 'unknown' | null
  missing_fields: string[]
}

export type CaseSummary = {
  id: string
  created_by_user_id: string
  scenario_id: string
  synthetic: true
  status: CaseStatus
  state_version: number
  content_revision: number
  created_at: string
  updated_at: string
  intake: Intake
}

export type ProviderResult = {
  id: string
  source_id: string
  provider: string
  mode: 'mock' | 'live'
  status: ProviderStatus
  source_version: string
  query_hash: string
  query: Record<string, unknown>
  retrieved_at: string
  data: Record<string, unknown>
  reason: string | null
}

export type EvidenceKind = 'scene_photo' | 'vehicle_photo' | 'damage_photo' | 'document' | 'other' | 'scene_video' | 'cctv_video' | 'insured_video'

export type Evidence = {
  id: string
  case_id: string
  kind: string
  storage_path: string
  source_kind: string
  mode: 'mock' | 'live'
  mime_type: string
  byte_size: number
  client_sha256: string | null
  checksum_status: 'client_declared' | 'verified' | 'mismatch'
  sha256_verified: string | null
  role: string | null
  display_order: number | null
  original_filename: string | null
  received_at: string
}

export type EvidenceUploadIntent = {
  bucket: string
  storage_path: string
  token: string
  mime_type: string
  byte_size: number
  expires_at: string
}

export type EvidenceReadUrl = {
  evidence_id: string
  url: string
  expires_at: string
}

export type VideoAnalysisLink = {
  evidence_id: string
  artifact_id: string
  pipeline_fingerprint: string
  observations: { start_ms: number; end_ms: number; category: string; description: string; uncertainty: string | null }[]
}

export type CameraCandidate = {
  id: string
  label: string
  source: string
  controller: string | null
  recipient: string | null
  likely_from: string | null
  likely_to: string | null
  status: 'candidate' | 'unknown_owner'
  fixture_event_id: string | null
  mode: 'mock'
}

export type CameraSearch = {
  status: 'candidates' | 'unavailable'
  mode: 'mock'
  source_version: string
  reason: string | null
  candidates: CameraCandidate[]
}

export type CameraMap = {
  mode?: 'mock'
  status: 'available' | 'no_cameras' | 'not_covered' | 'unresolved' | 'unavailable'
  address: string | null
  resolved_address: string | null
  latitude: number | null
  longitude: number | null
  radius_m: number
  cameras: {
    id: string
    latitude: number
    longitude: number
    distance_m: number
    label: string
    operator: string | null
    camera_type: string | null
    source_url: string | null
  }[]
  reason: string | null
}

export type CameraRequest = {
  id: string
  case_id: string
  candidate_id: string
  candidate_label: string
  source: string
  controller: string | null
  recipient: string | null
  scope: string
  reason: string
  status: 'draft' | 'requested' | 'waiting' | 'denied' | 'unknown_owner' | 'received' | 'unavailable' | 'timed_out'
  mode: 'mock'
  fixture_event_id: string | null
  evidence_id: string | null
  created_by_user_id: string
  approved_by_user_id: string | null
  status_actor_user_id: string | null
  created_at: string
  approved_at: string | null
  requested_at: string | null
  status_changed_at: string | null
  received_at: string | null
}

export type VideoAnalysisResponse = {
  status: 'reused' | 'completed' | 'processing' | 'not_preprocessed'
  artifact_id: string | null
  pipeline_fingerprint: string
  analysis_reused: boolean
  observations: { observation: VideoAnalysisLink['observations'][number]; source_ref: AnalysisSourceRef }[]
  estimated_cost_saved_minor: number | null
  pipeline_changed: boolean
}

export type VisualFact = {
  media_id: string
  source_ref: AnalysisSourceRef
  category: 'vehicle' | 'plate' | 'damage' | 'movement' | 'other'
  status: 'observed' | 'uncertain' | 'not_visible'
  text: string
  vehicle_track_id: string | null
  plate_candidate: string | null
  uncertain_positions: number[]
  confidence: number | null
  calibration: string | null
  zone: { x: number; y: number; width: number; height: number } | null
  start_ms: number | null
  end_ms: number | null
}

export type VisionResponse = {
  status: 'observed' | 'uncertain' | 'not_visible' | 'processing' | 'not_preprocessed' | 'unavailable' | 'error'
  provider_result_id: string | null
  mode: 'mock' | 'live' | null
  source_version: string
  observations: VisualFact[]
  reason: string | null
  analysis_reused: boolean
}

export type CreateEvidenceIntent = {
  filename: string
  mime_type: string
  byte_size: number
  client_sha256: string
  kind: EvidenceKind
  expected_state_version: number
}

export type AuditEvent = {
  id: string
  actor_user_id: string | null
  event_type: string
  state_version_before: number
  state_version_after: number
  content_revision_before: number
  content_revision_after: number
  metadata: Record<string, unknown>
  occurred_at: string
}

export type AnalysisSourceRef = {
  kind: 'intake' | 'evidence' | 'provider_result' | 'estimate' | 'voice'
  id: string
  locator: string
}

export type ReportLine = {
  id: string
  text: string
  claim_kind: 'declaration' | 'observation' | 'provider_result' | 'hypothesis' | 'handler_edit'
  source_refs: AnalysisSourceRef[]
  uncertainty: string | null
  as_of_revision: number
  stale: boolean
  mode: 'mock' | 'live' | null
  signed_by: string | null
  signed_at: string | null
  previous_text: string | null
}

export type EstimateItem = { id: string; label: string; amount_minor: number }
export type Estimate = {
  id: string
  case_id: string
  version: number
  line_items: EstimateItem[]
  total_minor: number
  currency: 'EUR'
  tax_basis: 'TTC'
  estimate_source: 'demo_fixture' | 'handler' | 'quote' | 'agent_proposal'
  source_refs: AnalysisSourceRef[]
  created_by_user_id: string | null
  created_at: string
}

export type Quote = {
  id: string
  case_id: string
  version: number
  evidence_id: string
  filename: string
  mime_type: 'application/pdf'
  checksum: string
  checksum_status: 'client_declared' | 'verified'
  total_ttc_minor: number
  amount_source: 'handler_entered' | 'demo_fixture'
  attached_estimate_version: number | null
  created_by_user_id: string
  created_at: string
}

export type AnalysisProposition = {
  text: string
  assessment: 'supported' | 'hypothesis' | 'contradicted'
  source_refs: AnalysisSourceRef[]
  uncertainty_note: string | null
}

export type AnalysisIssue = { text: string; source_refs: AnalysisSourceRef[] }
export type MediaCitation = { evidence_id: string; timestamp_seconds: number | null }
export type VisualFinding = { description: string; confidence: 'low' | 'medium' | 'high'; citations: MediaCitation[] }
export type RepairEstimate = { minimum_minor: number; maximum_minor: number; currency: string; assumptions: string;
  line_items?: { label: string; minimum_minor: number; maximum_minor: number }[] }
export type MediaAnalysis = {
  analyzed_evidence_ids: string[]
  summary: string
  observations: VisualFinding[]
  plates: (VisualFinding & { vehicle: string; plate: string | null; legibility: string; role: string })[]
  damages: (VisualFinding & { vehicle: string; affected_parts: string[]; severity: string; accident_link: string;
    estimate: RepairEstimate | null })[]
  liability: { likely_responsible: string; reasoning: string; confidence: string; citations: MediaCitation[]; limitations: string[]; requires_human_review: true;
    vehicle_assessments?: { vehicle: string; role: string; assessment: string; reasoning: string; confidence: string; citations: MediaCitation[] }[] }
  cross_evidence_consistency: string
  limitations: string[]
}
export type AnalysisOutput = {
  schema_version: 1
  proposed_route: 'subrogation' | 'handler_review' | 'insufficient_information'
  recipient: { name: string; country: string; source_refs: AnalysisSourceRef[] } | null
  amount: { amount_minor: number; currency: string; source_refs: AnalysisSourceRef[] } | null
  propositions: AnalysisProposition[]
  contradictions: AnalysisIssue[]
  missing_items: string[]
  non_blocking_notes: string[]
  draft_body: string
  media_analysis?: MediaAnalysis | null
}

export type AnalysisGateResult = {
  gate: 'intake' | 'counterparty' | 'evidence' | 'approval'
  status: 'passed' | 'needs_review' | 'blocked'
  reason_codes: string[]
  source_refs: AnalysisSourceRef[]
}
export type GateResult = AnalysisGateResult

export type AnalysisRun = {
  id: string
  input_content_revision: number
  method_version: string
  status: 'running' | 'ready' | 'stale' | 'failed'
  mode: 'mock' | 'live'
  output: AnalysisOutput | null
  gate_results: AnalysisGateResult[]
  error_code: string | null
  error_message: string | null
  started_at: string
  finished_at: string | null
}

export type DraftView = {
  id: string
  case_id: string
  analysis_run_id: string | null
  parent_draft_id: string | null
  version: number
  content_revision: number
  recipient: Record<string, unknown> | null
  amount_minor: number | null
  currency: string | null
  body: string
  attachment_ids: string[]
  package: { schema_version: number; report_lines: { id: string; text: string; uncertainty: string | null; source_refs: unknown[] }[]; estimate: Estimate | null; quote: Quote | null; video_analyses: VideoAnalysisLink[] } | null
  transmission_comment: string
  created_by_user_id: string | null
  sha256: string
  created_at: string
}
export type Draft = DraftView

export type ApprovalView = {
  id: string
  case_id: string
  draft_id: string
  actor_user_id: string
  draft_sha256: string
  approved_content_revision: number
  approved_at: string
  superseded_at: string | null
}

export type ActionReceipt = {
  id: string
  case_id: string
  kind: 'registration' | 'send'
  mode: 'mock'
  status: 'confirmed' | 'unknown' | 'failed'
  approval_id: string
  draft_id: string
  idempotency_key: string
  reference: string | null
  created_at: string
  registration_action_id: string | null
  envelope: TransmissionReceipt | null
}

export type TransmissionPreview = {
  schema_version: number
  mode: 'mock'
  simulation: string
  case_id: string
  draft_id: string
  draft_sha256: string
  content_revision: number
  approval_id: string | null
  registration_action_id: string | null
  registration_reference: string | null
  recipient: Record<string, unknown> | null
  body: string
  transmission_comment: string
  amount_minor: number | null
  currency: string | null
  package: NonNullable<DraftView['package']>
  attachments: { id: string; kind: string; mime_type: string; filename: string | null; byte_size: number; checksum: string; checksum_status: string }[]
}
export type TransmissionReceipt = TransmissionPreview & { send_action_id: string; send_reference: string; sent_at: string }

export type CaseView = CaseSummary & {
  partner_garages?: PartnerGarage[]
  camera_requests: CameraRequest[]
  evidence: Evidence[]
  video_analyses: VideoAnalysisLink[]
  provider_results: ProviderResult[]
  latest_analysis: AnalysisRun | null
  current_draft: DraftView | null
  gate_results: GateResult[]
  approval: ApprovalView | null
  actions: ActionReceipt[]
  timeline: AuditEvent[]
  report: { lines: ReportLine[]; analysis_current: boolean; needs_reanalysis: boolean }
  estimate: Estimate | null
  quote: Quote | null
  quote_status: 'no_estimate' | 'no_quote' | 'matched' | 'mismatch' | 'outdated'
  voice_session?: {
    provider: 'vapi'
    session_id: string
    mode: 'mock' | 'live'
    telephony_provider: 'twilio' | 'web'
    status: 'urgent_human_handoff' | 'collecting' | 'complete' | 'incomplete' | 'error'
    missing_p0: string[]
    reason_codes: string[]
    facts: { field: string; value: string; excerpt: string; uncertainty: string }[]
    segments: { id: string; speaker: 'caller' | 'assistant'; text: string; start_ms: number | null; end_ms: number | null }[]
    recording: { status: 'pending' | 'available' | 'unavailable' | 'error'; mime_type: string | null; byte_size: number | null; sha256: string | null; error_code: string | null }
    call_started_at: string
  } | null
}

export type AnalysisRunResponse = { analysis_run: AnalysisRun; case: CaseView }

export type SmsRecipientConfirmation =
  | { kind: 'caller_id' }
  | { kind: 'manager_correction'; number: string; reason: string }

export type SmsPreview = {
  recipient_masked: string
  body: string
  missing_items: string[]
  source_refs: string[]
  deposit_grant_id: string
  deposit_url: string
  deposit_link_expires_at: string
  content_revision: number
  preview_hash: string
  warnings: string[]
}

export type SmsMessage = {
  id: string
  case_id: string
  recipient_masked: string
  body_preview: string
  mode: 'mock' | 'live'
  provider: string
  status: 'queued' | 'accepted' | 'sent' | 'delivered' | 'failed' | 'unknown'
  error_code: string | null
  created_at: string
  updated_at: string
  sent_at: string | null
  delivered_at: string | null
}

export type SmsCreateResponse = { message: SmsMessage; replayed: boolean; state_version: number }
export type WhatsAppInbound = { id: string; body: string; media_count: number; evidence_id: string | null; received_at: string }
export type PortalChatMessage = { id: string; sender: 'manager' | 'insured'; body: string; created_at: string }
export type SmsLinkPreview = Pick<SmsPreview, 'recipient_masked' | 'body' | 'deposit_grant_id' |
  'deposit_url' | 'deposit_link_expires_at' | 'content_revision' | 'preview_hash' | 'warnings'>

export type IntakePatch = Partial<Pick<Intake,
  | 'reported_at'
  | 'insured_name'
  | 'insured_reference'
  | 'insured_vehicle'
  | 'insured_plate'
  | 'policy_reference'
  | 'incident_at'
  | 'location'
  | 'vehicle_country'
  | 'narrative'
  | 'danger_status'
  | 'injury_status'
>>

type ErrorEnvelope = {
  error?: {
    code?: string
    message?: string
    details?: Record<string, unknown>
    request_id?: string
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string | undefined
  readonly details: Record<string, unknown>
  readonly requestId: string | undefined

  constructor(status: number, error: ErrorEnvelope['error']) {
    super(error?.message || `API request failed (${status})`)
    this.name = 'ApiError'
    this.status = status
    this.code = error?.code
    this.details = error?.details || {}
    this.requestId = error?.request_id
  }
}

async function request<T>(path: string, accessToken: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${accessToken}`,
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  })

  const body = await response.json().catch(() => ({})) as T & ErrorEnvelope
  if (!response.ok) throw new ApiError(response.status, body.error)
  return body as T
}

export async function getCurrentUser(accessToken: string): Promise<CurrentUser> {
  return request<CurrentUser>('/v1/me', accessToken)
}

export async function listCases(accessToken: string): Promise<CaseSummary[]> {
  return request<CaseSummary[]>('/v1/cases?limit=50', accessToken)
}

export type CaseListPage = { items: CaseSummary[]; total: number; offset: number; limit: number; has_more: boolean }

export async function listCasesPage(accessToken: string, options: { query?: string; offset?: number; limit?: number } = {}): Promise<CaseListPage> {
  const params = new URLSearchParams({ query: options.query ?? '', offset: String(options.offset ?? 0), limit: String(options.limit ?? 25) })
  return request<CaseListPage>(`/v1/cases/page?${params}`, accessToken)
}

export async function getCase(accessToken: string, caseId: string): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}`, accessToken)
}

export async function deleteCase(accessToken: string, caseId: string): Promise<void> {
  await request(`/v1/cases/${encodeURIComponent(caseId)}`, accessToken, { method: 'DELETE' })
}

export async function getVoiceRecordingReadUrl(accessToken: string, caseId: string): Promise<{ url: string; expires_in: number; mime_type: string }> {
  return request(`/v1/voice/${encodeURIComponent(caseId)}/recording/read-url`, accessToken)
}

export async function reextractVoiceCall(accessToken: string, caseId: string): Promise<void> {
  await request(`/v1/voice/${encodeURIComponent(caseId)}/reextract`, accessToken, { method: 'POST' })
}

export type VoiceWebTestSession = { token: string; assistant_id: string; expires_at: string }
export type VoiceWebTestCall = { case_id: string; status: string; recording_status: string }

export async function createVoiceWebTestSession(accessToken: string): Promise<VoiceWebTestSession> {
  return request('/v1/voice/web-test/session', accessToken, { method: 'POST', cache: 'no-store' })
}

export async function getVoiceWebTestCall(accessToken: string, callId: string): Promise<VoiceWebTestCall> {
  return request(`/v1/voice/web-test/calls/${encodeURIComponent(callId)}`, accessToken, { cache: 'no-store' })
}

export async function getSmsRecipientCandidate(accessToken: string, caseId: string): Promise<{ recipient_masked: string }> {
  return request(`/v1/cases/${encodeURIComponent(caseId)}/messages/recipient-candidate`, accessToken, { cache: 'no-store' })
}

export async function previewSms(accessToken: string, caseId: string, expectedStateVersion: number, recipientConfirmation: SmsRecipientConfirmation): Promise<SmsPreview> {
  return request(`/v1/cases/${encodeURIComponent(caseId)}/messages/preview`, accessToken, {
    method: 'POST', cache: 'no-store',
    body: JSON.stringify({ expected_state_version: expectedStateVersion, recipient_confirmation: recipientConfirmation }),
  })
}

export async function createSms(accessToken: string, caseId: string, input: {
  expected_state_version: number
  idempotency_key: string
  recipient_confirmation: SmsRecipientConfirmation
  deposit_grant_id: string
  deposit_url: string
  preview_hash: string
}): Promise<SmsCreateResponse> {
  return request(`/v1/cases/${encodeURIComponent(caseId)}/messages`, accessToken, {
    method: 'POST', cache: 'no-store', body: JSON.stringify(input),
  })
}

export async function listSms(accessToken: string, caseId: string): Promise<SmsMessage[]> {
  return request(`/v1/cases/${encodeURIComponent(caseId)}/messages`, accessToken, { cache: 'no-store' })
}

export async function autoFakeSms(accessToken: string, caseId: string): Promise<SmsMessage> {
  return request(`/v1/cases/${encodeURIComponent(caseId)}/messages/auto-fake`, accessToken, {
    method: 'POST', cache: 'no-store',
  })
}

export async function listWhatsAppInbound(accessToken: string, caseId: string): Promise<WhatsAppInbound[]> {
  return request(`/v1/cases/${encodeURIComponent(caseId)}/messages/inbound`, accessToken, { cache: 'no-store' })
}

export async function getSmsDepositLink(accessToken: string, caseId: string, messageId: string): Promise<{ url: string; expires_at: string }> {
  return request(`/v1/cases/${encodeURIComponent(caseId)}/messages/${encodeURIComponent(messageId)}/link`, accessToken, { cache: 'no-store' })
}

const smsLinkPath = (caseId: string) => `/v1/cases/${encodeURIComponent(caseId)}/sms-links`

export async function getSmsLinkCandidate(accessToken: string, caseId: string): Promise<{ recipient_masked: string }> {
  return request(`${smsLinkPath(caseId)}/recipient-candidate`, accessToken, { cache: 'no-store' })
}

export async function previewSmsLink(accessToken: string, caseId: string, expectedStateVersion: number,
  recipientConfirmation: SmsRecipientConfirmation): Promise<SmsLinkPreview> {
  return request(`${smsLinkPath(caseId)}/preview`, accessToken, { method: 'POST', cache: 'no-store',
    body: JSON.stringify({ expected_state_version: expectedStateVersion, recipient_confirmation: recipientConfirmation }) })
}

export async function sendSmsLink(accessToken: string, caseId: string, input: {
  expected_state_version: number; idempotency_key: string; recipient_confirmation: SmsRecipientConfirmation
  recipient_consent_confirmed: boolean; deposit_grant_id: string; deposit_url: string; preview_hash: string
}): Promise<SmsCreateResponse> {
  return request(smsLinkPath(caseId), accessToken, { method: 'POST', cache: 'no-store', body: JSON.stringify(input) })
}

export async function listSmsLinks(accessToken: string, caseId: string): Promise<SmsMessage[]> {
  return request(smsLinkPath(caseId), accessToken, { cache: 'no-store' })
}

export async function getSmsLink(accessToken: string, caseId: string, messageId: string): Promise<{ url: string; expires_at: string }> {
  return request(`${smsLinkPath(caseId)}/${encodeURIComponent(messageId)}/link`, accessToken, { cache: 'no-store' })
}

export async function listPortalChat(accessToken: string, caseId: string, messageId: string): Promise<PortalChatMessage[]> {
  return request(`${smsLinkPath(caseId)}/${encodeURIComponent(messageId)}/conversation`, accessToken, { cache: 'no-store' })
}

export async function replyPortalChat(accessToken: string, caseId: string, messageId: string,
  clientMessageId: string, body: string): Promise<PortalChatMessage> {
  return request(`${smsLinkPath(caseId)}/${encodeURIComponent(messageId)}/conversation`, accessToken,
    { method: 'POST', cache: 'no-store', body: JSON.stringify({ client_message_id: clientMessageId, body }) })
}

export async function createCase(accessToken: string, scenarioId: 'g1' | 'g2' | 'g3' | 'complete' | 'ambiguous'): Promise<CaseView> {
  return request<CaseView>('/v1/cases', accessToken, {
    method: 'POST',
    body: JSON.stringify({ scenario_id: scenarioId }),
  })
}

export async function updateIntake(
  accessToken: string,
  caseId: string,
  expectedStateVersion: number,
  patch: IntakePatch,
): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/intake`, accessToken, {
    method: 'PATCH',
    body: JSON.stringify({ ...patch, expected_state_version: expectedStateVersion }),
  })
}

export async function editReportLine(
  accessToken: string, caseId: string, lineId: string,
  text: string, uncertainty: string | null, expectedStateVersion: number,
): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/report-lines/${encodeURIComponent(lineId)}`, accessToken, {
    method: 'PATCH', body: JSON.stringify({ text, uncertainty, expected_state_version: expectedStateVersion }),
  })
}

export async function upsertEstimate(
  accessToken: string, caseId: string, expectedStateVersion: number,
  lineItems: EstimateItem[], totalMinor: number,
): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/estimate`, accessToken, {
    method: 'PUT', body: JSON.stringify({
      line_items: lineItems, total_minor: totalMinor, currency: 'EUR', tax_basis: 'TTC',
      estimate_source: 'handler', source_refs: [], expected_state_version: expectedStateVersion,
    }),
  })
}

export async function attachQuote(
  accessToken: string, caseId: string, expectedStateVersion: number,
  evidenceId: string, totalTtcMinor: number,
): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/quote`, accessToken, {
    method: 'PUT', body: JSON.stringify({
      evidence_id: evidenceId, total_ttc_minor: totalTtcMinor,
      amount_source: 'handler_entered', expected_state_version: expectedStateVersion,
    }),
  })
}

export async function removeQuote(
  accessToken: string, caseId: string, expectedStateVersion: number,
): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/quote`, accessToken, {
    method: 'DELETE', body: JSON.stringify({ expected_state_version: expectedStateVersion }),
  })
}

export async function runAnalysis(
  accessToken: string,
  caseId: string,
  expectedStateVersion: number,
): Promise<AnalysisRunResponse> {
  return request<AnalysisRunResponse>(`/v1/cases/${encodeURIComponent(caseId)}/analysis-runs`, accessToken, {
    method: 'POST',
    body: JSON.stringify({ expected_state_version: expectedStateVersion }),
  })
}

export async function createEvidenceUploadIntent(
  accessToken: string,
  caseId: string,
  input: CreateEvidenceIntent,
): Promise<EvidenceUploadIntent> {
  return request<EvidenceUploadIntent>(`/v1/cases/${encodeURIComponent(caseId)}/evidence/upload-intents`, accessToken, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function finalizeEvidence(
  accessToken: string,
  caseId: string,
  storagePath: string,
  clientSha256: string,
  kind: EvidenceKind,
  expectedStateVersion: number,
): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/evidence`, accessToken, {
    method: 'POST',
    body: JSON.stringify({
      storage_path: storagePath,
      client_sha256: clientSha256,
      kind,
      expected_state_version: expectedStateVersion,
    }),
  })
}

export async function getEvidenceReadUrl(
  accessToken: string,
  caseId: string,
  evidenceId: string,
): Promise<EvidenceReadUrl> {
  return request<EvidenceReadUrl>(
    `/v1/cases/${encodeURIComponent(caseId)}/evidence/${encodeURIComponent(evidenceId)}/read-url`,
    accessToken,
  )
}

export async function runVideoAnalysis(
  accessToken: string,
  caseId: string,
  evidenceId: string,
  processingPolicy: 'reuse_only' | 'allow_new',
): Promise<VideoAnalysisResponse> {
  return request<VideoAnalysisResponse>(
    `/v1/cases/${encodeURIComponent(caseId)}/evidence/${encodeURIComponent(evidenceId)}/video-analysis`,
    accessToken,
    { method: 'POST', body: JSON.stringify({ processing_policy: processingPolicy }) },
  )
}

export async function runVision(
  accessToken: string, caseId: string, evidenceId: string,
  processingPolicy: 'reuse_only' | 'allow_new',
): Promise<VisionResponse> {
  return request<VisionResponse>(`/v1/cases/${encodeURIComponent(caseId)}/vision-observations`, accessToken, {
    method: 'POST', body: JSON.stringify({ evidence_id: evidenceId, processing_policy: processingPolicy }),
  })
}

export async function receiveCctv(
  accessToken: string,
  caseId: string,
  fixtureEventId: string,
  expectedStateVersion: number,
  cameraRequestId?: string,
): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/demo-events/cctv-received`, accessToken, {
    method: 'POST',
    body: JSON.stringify({ fixture_event_id: fixtureEventId, expected_state_version: expectedStateVersion,
      camera_request_id: cameraRequestId || null }),
  })
}

export async function searchCameras(accessToken: string, caseId: string): Promise<CameraSearch> {
  return request<CameraSearch>(`/v1/cases/${encodeURIComponent(caseId)}/cameras/search`, accessToken)
}

export async function mapCameras(accessToken: string, caseId: string, radiusM = 150): Promise<CameraMap> {
  return request<CameraMap>(`/v1/cases/${encodeURIComponent(caseId)}/cameras/map?radius_m=${radiusM}`, accessToken)
}

export async function createCameraRequest(accessToken: string, caseId: string,
  candidateId: string, scope: string, reason: string, expectedStateVersion: number): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/camera-requests`, accessToken, {
    method: 'POST', body: JSON.stringify({ candidate_id: candidateId, scope, reason,
      expected_state_version: expectedStateVersion }),
  })
}

export async function approveCameraRequest(accessToken: string, caseId: string,
  requestId: string, expectedStateVersion: number): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/camera-requests/${encodeURIComponent(requestId)}/approve`, accessToken, {
    method: 'POST', body: JSON.stringify({ expected_state_version: expectedStateVersion }),
  })
}

export async function updateCameraRequestStatus(accessToken: string, caseId: string,
  requestId: string, status: 'waiting' | 'denied' | 'unknown_owner' | 'unavailable' | 'timed_out',
  expectedStateVersion: number): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/camera-requests/${encodeURIComponent(requestId)}/status`, accessToken, {
    method: 'PATCH', body: JSON.stringify({ status, expected_state_version: expectedStateVersion }),
  })
}

export async function seedG1Media(accessToken: string, caseId: string, expectedStateVersion: number): Promise<CaseView> {
  return request<CaseView>(`/v1/cases/${encodeURIComponent(caseId)}/demo-events/g1-media`, accessToken, {
    method: 'POST',
    body: JSON.stringify({ expected_state_version: expectedStateVersion }),
  })
}

export async function updateCurrentDraft(
  accessToken: string,
  caseId: string,
  expectedStateVersion: number,
  patch: Pick<DraftView, 'recipient' | 'amount_minor' | 'currency' | 'body' | 'attachment_ids' | 'transmission_comment'>,
): Promise<DraftView> {
  return request<DraftView>(`/v1/cases/${encodeURIComponent(caseId)}/drafts/current`, accessToken, {
    method: 'PATCH',
    body: JSON.stringify({ ...patch, expected_state_version: expectedStateVersion }),
  })
}

export async function approveDraft(
  accessToken: string,
  caseId: string,
  draftId: string,
  draftSha256: string,
  expectedStateVersion: number,
): Promise<ApprovalView> {
  return request<ApprovalView>(`/v1/cases/${encodeURIComponent(caseId)}/approvals`, accessToken, {
    method: 'POST',
    body: JSON.stringify({ draft_id: draftId, draft_sha256: draftSha256, confirmed_review: true, expected_state_version: expectedStateVersion }),
  })
}

export async function getTransmissionPreview(accessToken: string, caseId: string): Promise<TransmissionPreview> {
  return request<TransmissionPreview>(`/v1/cases/${encodeURIComponent(caseId)}/transmission/preview`, accessToken)
}

export async function downloadTransmissionReceipt(accessToken: string, caseId: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/v1/cases/${encodeURIComponent(caseId)}/transmission/receipt`, {
    headers: { Authorization: `Bearer ${accessToken}`, Accept: 'application/json' },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as ErrorEnvelope
    throw new ApiError(response.status, body.error)
  }
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = `simulation-receipt-${caseId}.json`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

export async function simulateRegistration(
  accessToken: string,
  caseId: string,
  expectedStateVersion: number,
  idempotencyKey: string,
): Promise<ActionReceipt> {
  return request<ActionReceipt>(`/v1/cases/${encodeURIComponent(caseId)}/registration`, accessToken, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ expected_state_version: expectedStateVersion }),
  })
}

export async function simulateSend(
  accessToken: string,
  caseId: string,
  expectedStateVersion: number,
  idempotencyKey: string,
): Promise<ActionReceipt> {
  return request<ActionReceipt>(`/v1/cases/${encodeURIComponent(caseId)}/send`, accessToken, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ expected_state_version: expectedStateVersion }),
  })
}

export async function checkApi(): Promise<boolean> {
  try {
    const response = await fetch(`${apiBaseUrl}/health/live`)
    return response.ok
  } catch {
    return false
  }
}


export async function runMediaAnalysis(accessToken: string, caseId: string, expectedStateVersion: number): Promise<AnalysisRunResponse> {
  return request<AnalysisRunResponse>(`/v1/cases/${encodeURIComponent(caseId)}/media-analysis-runs`, accessToken, {
    method: 'POST', body: JSON.stringify({ expected_state_version: expectedStateVersion }),
  })
}

export type CorrespondenceDraft = {
  id: string; kind: 'garage' | 'cctv'; content_revision: number; recipient: string; subject: string;
  body: string; source_url: string | null; version: number; status: string; error_code: string | null;
}
export type MediaWorkflowView = {
  content_revision: number; email_configured: boolean; worker_configured: boolean;
  workflow: { status: string; error_code: string | null; updated_at: string;
    insurance_matches: { vehicle: string; plate: string | null; status: string; reason: string | null;
      data: { insurer_name?: string } }[] } | null;
  correspondence: CorrespondenceDraft[];
}
export const getMediaWorkflow = (token: string, id: string) => request<MediaWorkflowView>(`/v1/cases/${encodeURIComponent(id)}/automation`, token)
export const advanceMediaWorkflow = (token: string, id: string) => request<MediaWorkflowView>(`/v1/cases/${encodeURIComponent(id)}/automation/advance`, token, { method: 'POST' })
export const retryMediaWorkflow = (token: string, id: string, version: number) => request<MediaWorkflowView>(`/v1/cases/${encodeURIComponent(id)}/automation/retry`, token, { method: 'POST', body: JSON.stringify({ expected_state_version: version }) })
export const createCameraMail = (token: string, id: string, body: { expected_state_version: number; camera_label: string; controller: string; recipient: string; source_url: string; recipient_confirmed: boolean }) =>
  request<CorrespondenceDraft>(`/v1/cases/${encodeURIComponent(id)}/correspondence/cctv`, token, { method: 'POST', body: JSON.stringify(body) })
export const editCorrespondence = (token: string, id: string, draft: string, body: { version: number; recipient: string; subject: string; body: string }) =>
  request<CorrespondenceDraft>(`/v1/cases/${encodeURIComponent(id)}/correspondence/${encodeURIComponent(draft)}`, token, { method: 'PUT', body: JSON.stringify(body) })
export const sendCorrespondence = (token: string, id: string, draft: string, version: number) =>
  request<CorrespondenceDraft>(`/v1/cases/${encodeURIComponent(id)}/correspondence/${encodeURIComponent(draft)}/send`, token, { method: 'POST', body: JSON.stringify({ version, confirm_send: true }) })

export type NotificationPreview = {
  channel: 'sms' | 'email'; recipient: string | null; mode: 'simulated' | 'live'
  status: 'ready' | 'unavailable'; reason: string | null
  subject?: string | null; body?: string
}

export type AccidentJourneyView = {
  notification_preview?: NotificationPreview[]
  content_revision: number; model: string; catalogue_count: number; notification_mode: 'simulated' | 'live'
  review: null | {
    id: string; status: 'needs_information' | 'awaiting_review' | 'approved'; blockers: string[]
    model: string; approved_amount_minor: number | null; amendment_reason: string | null
    insurance_matches: { vehicle: string; plate: string | null; status: string; data: { insurer_name?: string; driver_name?: string; insurer_email?: string } }[]
    assessment: {
      selected_video_id: string | null; match_reasoning: string; insured_vehicle: string | null; at_fault_vehicle: string | null
      involved_vehicles?: string[]; key_facts?: string[]
      video_candidates: { video_id: string; compatibility: 'strong' | 'possible' | 'incompatible'; reasons: string }[]
      repair_estimate: RepairEstimate | null
      missing_information: string[]; media: MediaAnalysis
    }
  }
  notifications: { id: string; channel: 'sms' | 'email'; recipient: string; subject: string; body: string; mode: 'simulated' | 'live'; status: string; error_code: string | null }[]
}
export const getAccidentJourney = (token: string, id: string) => request<AccidentJourneyView>(`/v1/cases/${encodeURIComponent(id)}/journey`, token)
export type PhotoVideoMatch = { status: 'pending' | 'no_match' | 'ambiguous' | 'matched'; photo_id: string; plate: string | null; video_id: string | null }
export const getPhotoVideoMatch = (token: string, caseId: string, photoId: string) =>
  request<PhotoVideoMatch>(`/v1/cases/${encodeURIComponent(caseId)}/journey/photos/${encodeURIComponent(photoId)}/video-match`, token)
export const approveAccident = (token: string, id: string, body: { review_id: string; expected_content_revision: number; amount_minor: number; amendment_reason: string }) =>
  request<AccidentJourneyView>(`/v1/cases/${encodeURIComponent(id)}/journey/approve`, token, { method: 'POST', body: JSON.stringify(body) })
export const editAccidentEstimate = (token: string, id: string, body: { review_id: string; expected_content_revision: number; minimum_minor: number; maximum_minor: number; line_items: { minimum_minor: number; maximum_minor: number }[] }) =>
  request<AccidentJourneyView>(`/v1/cases/${encodeURIComponent(id)}/journey/estimate`, token, { method: 'PUT', body: JSON.stringify(body) })
export const sendAccidentNotifications = (token: string, id: string, body: { review_id: string; expected_content_revision: number; confirm_send: true }) =>
  request<AccidentJourneyView>(`/v1/cases/${encodeURIComponent(id)}/journey/send`, token, { method: 'POST', body: JSON.stringify(body) })
export async function accidentVideo(token: string, id: string, videoId: string): Promise<string> {
  const response = await fetch(`${apiBaseUrl}/v1/cases/${encodeURIComponent(id)}/journey/video/${encodeURIComponent(videoId)}`, { headers: { Authorization: `Bearer ${token}` } })
  if (!response.ok) throw new Error('Impossible de charger la vidéo.')
  return URL.createObjectURL(await response.blob())
}

export type GaragePoint = { latitude: number; longitude: number }
export type PartnerGarage = GaragePoint & {
  id: string
  name: string
  address: string
  distance_m: number
  network_status: 'approved'
}
export type GaragePreview = {
  status: 'ready' | 'no_results' | 'needs_location'
  origin: GaragePoint | null
  radius_m: number | null
  garages: (GaragePoint & { osm_id: string; name: string; address: string | null; distance_m: number; directions_url: string })[]
  location_candidates: (GaragePoint & { label: string; precise: boolean })[]
  sms_body: string | null
  attribution: string
  attribution_url: string
}
export type GarageSmsState = {
  mode: 'mock' | 'live'
  trigger: 'mms' | 'photos'
  location: string | null
  settings: { recipient: string; enabled: boolean; origin_json: GaragePoint | null; location_text: string | null } | null
  jobs: { id: string; status: string; mode: 'mock' | 'live'; error_code: string | null; delivery_status: string | null; sms_body: string | null; created_at: string }[]
}
export async function previewGarages(token: string, caseId: string, version: number, origin: GaragePoint | null): Promise<GaragePreview> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 25_000)
  try {
    return await request(`/v1/cases/${encodeURIComponent(caseId)}/garage-preview`, token, {
      method: 'POST', body: JSON.stringify({ expected_state_version: version, origin }), signal: controller.signal,
    })
  } finally {
    clearTimeout(timer)
  }
}
export function getGarageSms(token: string, caseId: string): Promise<GarageSmsState> {
  return request(`/v1/cases/${encodeURIComponent(caseId)}/garage-sms`, token)
}
export function configureGarageSms(token: string, caseId: string, version: number, recipient: string, enabled: boolean, origin: GaragePoint | null): Promise<GarageSmsState> {
  return request(`/v1/cases/${encodeURIComponent(caseId)}/garage-sms`, token, {
    method: 'PATCH', body: JSON.stringify({ expected_state_version: version, recipient, enabled, origin }),
  })
}
export function queueGarageSms(token: string, caseId: string): Promise<GarageSmsState> {
  return request(`/v1/cases/${encodeURIComponent(caseId)}/garage-sms/photo-received`, token, { method: 'POST' })
}
