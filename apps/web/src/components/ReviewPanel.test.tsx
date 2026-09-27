// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { CaseView } from '../lib/api'
import { ApiError, approveDraft, simulateRegistration, simulateSend, updateCurrentDraft } from '../lib/api'
import { hasCurrentAnalysis } from '../lib/presentation'
import { ReviewPanel } from './ReviewPanel'

vi.mock('../lib/api', () => ({
  ApiError: class extends Error { status = 409; details = {} },
  approveDraft: vi.fn().mockResolvedValue({}), updateCurrentDraft: vi.fn().mockResolvedValue({}),
  simulateRegistration: vi.fn().mockResolvedValue({ reference: 'DEMO-REG', status: 'confirmed' }),
  simulateSend: vi.fn().mockResolvedValue({ reference: 'DEMO-SEND', status: 'confirmed' }),
  getTransmissionPreview: vi.fn().mockResolvedValue({ draft_id: 'draft', draft_sha256: 'hash', content_revision: 2,
    mode: 'mock', simulation: 'SIMULATION', recipient: { name: 'Assureur fictif' }, amount_minor: 124000,
    currency: 'EUR', body: 'Demande de recours', transmission_comment: '', registration_reference: null,
    package: { report_lines: [], quote: { filename: 'devis.pdf' } }, attachments: [] }),
  downloadTransmissionReceipt: vi.fn().mockResolvedValue(undefined),
}))
afterEach(() => { cleanup(); vi.clearAllMocks() })

function dossier(status: CaseView['status'] = 'review_ready'): CaseView {
  const approved = ['approved', 'registered', 'sent'].includes(status)
  return {
    id: 'case', status, state_version: 3, content_revision: 2, evidence: [],
    estimate: { total_minor: 124000 }, quote: { evidence_id: 'pdf', filename: 'devis.pdf' }, quote_status: 'matched',
    gate_results: ['intake', 'counterparty', 'evidence'].map(gate => ({ gate, status: 'passed', reason_codes: [], source_refs: [] })),
    current_draft: { id: 'draft', sha256: 'hash', version: 1, content_revision: 2, recipient: { name: 'Assureur fictif' }, amount_minor: 124000, currency: 'EUR', body: 'Demande de recours', attachment_ids: ['pdf'], transmission_comment: '', created_at: '2026-09-25T12:00:00Z' },
    approval: approved ? { draft_id: 'draft', draft_sha256: 'hash', approved_content_revision: 2, superseded_at: null } : null,
    actions: status === 'registered' || status === 'sent' ? [{ kind: 'registration', status: 'confirmed', reference: 'DEMO-REG' }] : [],
  } as unknown as CaseView
}

const show = (view: CaseView) => render(<ReviewPanel caseView={view} accessToken="test-token" onRefresh={vi.fn()} />)

it('offers approval before any transmission action and sends the exact version to the API', async () => {
  show(dossier())
  expect(screen.queryByRole('button', { name: /transmission|envoi/i })).toBeNull()
  await waitFor(() => expect(screen.getByLabelText(/J’ai examiné le récit/i)).toBeTruthy())
  fireEvent.click(screen.getByLabelText(/J’ai examiné le récit/i))
  fireEvent.click(screen.getByRole('button', { name: 'Valider cette version' }))
  await waitFor(() => expect(approveDraft).toHaveBeenCalledWith('test-token', 'case', 'draft', 'hash', 3))
  expect(simulateSend).not.toHaveBeenCalled()
})

it('keeps approval disabled and links to the unresolved controls', () => {
  const view = dossier()
  view.gate_results[1].status = 'blocked'
  show(view)
  const approve = screen.getByRole('button', { name: 'Valider cette version' }) as HTMLButtonElement
  expect(approve.disabled).toBe(true)
  expect(screen.getByRole('link', { name: 'Voir les contrôles du dossier' }).getAttribute('href')).toBe('#analysis')
  fireEvent.click(approve)
  expect(approveDraft).not.toHaveBeenCalled()
})

it('offers registration after approval, then saving alone when the manager edits', () => {
  show(dossier('approved'))
  expect(screen.getByRole('button', { name: 'Préparer la transmission simulée' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Confirmer l’envoi simulé' })).toBeNull()
  fireEvent.change(screen.getByLabelText('Message de recours'), { target: { value: 'Message amendé' } })
  expect(screen.getByRole('button', { name: 'Enregistrer les modifications' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Préparer la transmission simulée' })).toBeNull()
  expect(simulateRegistration).not.toHaveBeenCalled()
})

it('offers sending only after a confirmed registration', async () => {
  show(dossier('registered'))
  expect(screen.queryByRole('button', { name: 'Valider cette version' })).toBeNull()
  await waitFor(() => expect(screen.getByLabelText(/J’ai lu cet aperçu/i)).toBeTruthy())
  fireEvent.click(screen.getByLabelText(/J’ai lu cet aperçu/i))
  fireEvent.click(screen.getByRole('button', { name: 'Confirmer l’envoi simulé' }))
  await waitFor(() => expect(simulateSend).toHaveBeenCalledWith('test-token', 'case', 3, expect.any(String)))
})

it.each(['superseded', 'changed content', 'changed draft'])('does not offer transmission with a %s approval', reason => {
  const view = dossier('approved')
  if (reason === 'superseded') view.approval!.superseded_at = '2026-09-25T12:00:00Z'
  if (reason === 'changed content') view.content_revision += 1
  if (reason === 'changed draft') view.current_draft!.sha256 = 'new-hash'
  show(view)
  expect(screen.queryByRole('button', { name: /transmission|envoi/i })).toBeNull()
})

it('does not mark failed or outdated analysis as completed', () => {
  const view = dossier()
  view.latest_analysis = { status: 'failed', input_content_revision: 2 } as CaseView['latest_analysis']
  expect(hasCurrentAnalysis(view)).toBe(false)
  view.latest_analysis!.status = 'ready'
  view.content_revision = 3
  expect(hasCurrentAnalysis(view)).toBe(false)
})


it('preserves the server approval block even when upstream checks pass', () => {
  const view = dossier()
  view.gate_results.push({ gate: 'approval', status: 'blocked', reason_codes: ['quote_no_quote'], source_refs: [] })
  show(view)
  expect(screen.getByText('Ajouter le devis de réparation.')).toBeTruthy()
  const approve = screen.getByRole('button', { name: 'Valider cette version' }) as HTMLButtonElement
  expect(approve.disabled).toBe(true)
  fireEvent.click(approve)
  expect(approveDraft).not.toHaveBeenCalled()
})

it('shows a diff and requires explicit re-entry after a version conflict', async () => {
  vi.mocked(updateCurrentDraft).mockRejectedValueOnce(new ApiError(409, { code: 'stale_case', message: 'conflit', details: { current_state_version: 4 } }))
  const refresh = vi.fn()
  const current = dossier()
  const view = render(<ReviewPanel caseView={current} accessToken="test-token" onRefresh={refresh} />)
  fireEvent.change(screen.getByLabelText('Message de recours'), { target: { value: 'Mon message amendé' } })
  expect(screen.getByText('Aperçu des différences avant sauvegarde')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Enregistrer les modifications' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce())
  const newer = dossier()
  newer.state_version = 4
  newer.current_draft!.id = 'draft-new'
  newer.current_draft!.sha256 = 'new-hash'
  newer.current_draft!.version = 2
  newer.current_draft!.body = 'Message de l’autre onglet'
  view.rerender(<ReviewPanel caseView={newer} accessToken="test-token" onRefresh={refresh} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Ressaisir mes valeurs sur la version courante' }))
  expect((screen.getByLabelText('Message de recours') as HTMLTextAreaElement).value).toBe('Mon message amendé')
  expect(updateCurrentDraft).toHaveBeenCalledTimes(1)
})
