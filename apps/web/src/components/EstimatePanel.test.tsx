// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { CaseView } from '../lib/api'
import { parseEuroCents } from '../lib/money'
import { EstimatePanel } from './EstimatePanel'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('parses exact cents and rejects rounding, negative and non-finite amounts', () => {
  expect(parseEuroCents('480')).toBe(48000)
  expect(parseEuroCents('180,50')).toBe(18050)
  expect(parseEuroCents('0')).toBe(0)
  for (const invalid of ['1.234', '-1', 'NaN', 'Infinity', '1e3', '90071992547410.00']) {
    expect(parseEuroCents(invalid)).toBeNull()
  }
})

it('shows both totals when a manually entered PDF quote differs from the estimate', () => {
  const caseView = {
    id: 'case', state_version: 3, status: 'collecting', evidence: [],
    estimate: { id: 'estimate', version: 1, total_minor: 124000, estimate_source: 'demo_fixture',
      line_items: [{ id: 'repair', label: 'Réparation', amount_minor: 124000 }] },
    quote: { id: 'quote', evidence_id: 'pdf', filename: 'devis.pdf', total_ttc_minor: 123000,
      checksum: 'a'.repeat(64), checksum_status: 'client_declared', attached_estimate_version: 1 },
    quote_status: 'mismatch',
  } as unknown as CaseView
  render(<EstimatePanel caseView={caseView} accessToken="token" onUpdated={() => {}} onOpenEvidence={() => {}} />)
  expect(screen.getByText(/hypothèse de démonstration/i)).toBeTruthy()
  expect(screen.getByText(/écart bloquant/i).textContent).toContain('1 240')
  expect(screen.getByText(/écart bloquant/i).textContent).toContain('1 230')
  expect(screen.getByText(/n’a pas été lu automatiquement/i)).toBeTruthy()
})

it('reassociates the selected PDF through the real API client with exact cents and the current version', async () => {
  const view = {
    id: 'case', state_version: 7, status: 'collecting',
    evidence: [{ id: 'pdf', kind: 'document', mime_type: 'application/pdf', original_filename: 'devis.pdf' }],
    estimate: { id: 'estimate', version: 2, total_minor: 124050, estimate_source: 'handler', line_items: [] },
    quote: { id: 'quote', evidence_id: 'pdf', filename: 'devis.pdf', total_ttc_minor: 124000, attached_estimate_version: 1 },
    quote_status: 'outdated',
  } as unknown as CaseView
  const updated = { ...view, state_version: 8, quote_status: 'matched' }
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(updated)))
  vi.stubGlobal('fetch', fetchMock)
  const onUpdated = vi.fn()
  render(<EstimatePanel caseView={view} accessToken="test-token" onUpdated={onUpdated} onOpenEvidence={() => {}} />)
  fireEvent.change(screen.getByLabelText('Total TTC du PDF (€) · saisi manuellement'), { target: { value: '1240,50' } })
  fireEvent.click(screen.getByRole('button', { name: 'Réassocier le devis' }))
  await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(updated))
  const [url, request] = fetchMock.mock.calls[0]
  expect(url).toMatch(/\/v1\/cases\/case\/quote$/)
  expect(request.method).toBe('PUT')
  expect(request.headers.Authorization).toBe('Bearer test-token')
  expect(JSON.parse(request.body)).toEqual({ evidence_id: 'pdf', total_ttc_minor: 124050, amount_source: 'handler_entered', expected_state_version: 7 })
  expect(screen.getByText('Devis associé à l’estimation actuelle.')).toBeTruthy()
})
