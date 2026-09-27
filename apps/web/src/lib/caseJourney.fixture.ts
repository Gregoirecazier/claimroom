import type { CaseView } from './api'

export function reviewableCase(): CaseView {
  return {
    id: '00000000-0000-4000-8000-000000000001', status: 'review_ready', scenario_id: 'g1', state_version: 3, content_revision: 2,
    intake: { insured_reference: 'CLM-2026-0843', insured_name: 'Camille Martin', insured_vehicle: 'Peugeot grise', insured_plate: 'FR-482-KL', narrative: 'La BMW a reculé.', missing_fields: [], danger_status: 'no', injury_status: 'no', incident_at: '2026-09-25T10:00:00Z', location: 'Paris' },
    evidence: [{ id: 'photo', mime_type: 'image/png', original_filename: 'photo.png', kind: 'damage_photo', source_kind: 'demo_fixture', received_at: '2026-09-25T10:00:00Z' }],
    estimate: { total_minor: 124000, line_items: [{ label: 'Pare-chocs', amount_minor: 124000 }] }, quote: { evidence_id: 'pdf', filename: 'devis.pdf' }, quote_status: 'matched',
    latest_analysis: { status: 'ready', input_content_revision: 2 },
    gate_results: ['intake', 'counterparty', 'evidence'].map(gate => ({ gate, status: 'passed', reason_codes: [] })),
    current_draft: { id: 'draft', sha256: 'hash', content_revision: 2, transmission_comment: '', recipient: { name: 'Assureur démo' }, version: 1, amount_minor: 124000, currency: 'EUR', body: 'Demande de recours', attachment_ids: ['pdf'] },
    approval: null, actions: [], timeline: [], report: { lines: [], analysis_current: true, needs_reanalysis: false },
    camera_requests: [], video_analyses: [], provider_results: [],
  } as unknown as CaseView
}

