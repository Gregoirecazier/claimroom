// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { VisionPanel } from './VisionPanel'
import { reviewableCase } from '../lib/caseJourney.fixture'
import type { AnalysisRun } from '../lib/api'

afterEach(cleanup)
function analyzedCase() {
  const view = reviewableCase()
  view.evidence = [{ id: 'clip', case_id: view.id, kind: 'cctv_video', mime_type: 'video/webm', original_filename: 'cctv.webm' } as typeof view.evidence[number]]
  view.latest_analysis = { id: 'analysis-1', mode: 'live', gate_results: [], error_code: null, error_message: null, started_at: '2026-09-26T12:00:00Z', finished_at: '2026-09-26T12:01:00Z', status: 'ready', input_content_revision: view.content_revision,
    method_version: 'gemini-joint-media-v1:gemini-3.8-flash', output: { schema_version: 1, proposed_route: 'handler_review', recipient: null, amount: null, propositions: [], contradictions: [], missing_items: [], non_blocking_notes: [], draft_body: 'À vérifier', media_analysis: {
      analyzed_evidence_ids: ['clip'], summary: 'Collision visible sur la vidéo.',
      observations: [{ description: 'Le véhicule tourne.', confidence: 'medium', citations: [{ evidence_id: 'clip', timestamp_seconds: 2.5 }] }],
      plates: [], damages: [], liability: { likely_responsible: 'undetermined', confidence: 'low', requires_human_review: true, reasoning: 'Signalisation hors champ.', citations: [], limitations: [] },
      cross_evidence_consistency: 'Une seule vue.', limitations: ['À vérifier.'],
    } } } as AnalysisRun
  return view
}
it('shows saved Gemini observations with their original video citation', () => {
  const onOpenEvidence = vi.fn()
  render(<VisionPanel caseView={analyzedCase()} busy={false} onAnalyze={vi.fn()} onOpenEvidence={onOpenEvidence} />)
  expect(screen.getByText('Collision visible sur la vidéo.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', {name:'cctv.webm · 2.5 s'}))
  expect(onOpenEvidence).toHaveBeenCalledWith(expect.objectContaining({id:'clip'}))
  expect(screen.queryByText('Préparer la vidéo')).toBeNull()
})
it('hides obsolete findings and routes a retry through the joint media analysis action', () => {
  const view = analyzedCase(); view.content_revision++
  const onAnalyze = vi.fn()
  render(<VisionPanel caseView={view} busy={false} onAnalyze={onAnalyze} onOpenEvidence={vi.fn()} />)
  expect(screen.queryByText('Collision visible sur la vidéo.')).toBeNull()
  fireEvent.click(screen.getByRole('button', {name:'Analyser les pièces'}))
  expect(onAnalyze).toHaveBeenCalledOnce()
})
it('shows each vehicle, readable and unreadable plates, responsibility, costs and uncertainty', () => {
  const view = analyzedCase()
  view.latest_analysis!.method_version = 'gemini-joint-media-v2:gemini-3.8-flash:visual'
  const media = view.latest_analysis!.output!.media_analysis!
  const source = { description: 'Visible sur la vidéo', confidence: 'medium' as const, citations: [{ evidence_id: 'clip', timestamp_seconds: 2.5 }] }
  media.plates = [
    { ...source, vehicle: 'Peugeot', plate: 'FR-482-KL', legibility: 'readable', role: 'insured' },
    { ...source, vehicle: 'BMW', plate: null, legibility: 'unreadable', role: 'third_party' },
  ]
  media.damages = [
    { ...source, vehicle: 'Peugeot', affected_parts: ['Pare-chocs'], severity: 'moderate', accident_link: 'consistent', estimate: { minimum_minor: 50000, maximum_minor: 100000, currency: 'EUR', assumptions: 'Hors dégâts cachés' } },
    { ...source, vehicle: 'BMW', affected_parts: [], severity: 'unknown', accident_link: 'uncertain', estimate: null },
  ]
  media.liability.vehicle_assessments = [{ vehicle: 'BMW', role: 'third_party', assessment: 'possibly_contributing', reasoning: 'Trajectoire à confirmer.', confidence: 'low', citations: source.citations }]
  render(<VisionPanel caseView={view} busy={false} onAnalyze={vi.fn()} onOpenEvidence={vi.fn()} />)
  expect(screen.getByText('Peugeot · FR-482-KL')).toBeTruthy()
  expect(screen.getByText('BMW · Plaque illisible')).toBeTruthy()
  expect(screen.getByText('BMW · Contribution possible')).toBeTruthy()
  expect(screen.getByText(/Estimation indicative :/).textContent).toContain('500')
  expect(screen.getByText(/Chiffrage impossible/)).toBeTruthy()
})
it('explains provider quotas without displaying a raw HTTP error', () => {
  const view = analyzedCase()
  view.latest_analysis!.status = 'failed'
  view.latest_analysis!.error_code = 'gemini_rate_limited'
  view.latest_analysis!.error_message = 'Gemini generation request failed (HTTP 429)'
  render(<VisionPanel caseView={view} busy={false} onAnalyze={vi.fn()} onOpenEvidence={vi.fn()} />)
  expect(screen.getByRole('status').textContent).toContain('quota')
  expect(screen.getByRole('status').textContent).not.toContain('HTTP 429')
})
