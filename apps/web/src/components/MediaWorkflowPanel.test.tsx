// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { CorrespondenceEditor, MediaWorkflowPanel } from './MediaWorkflowPanel'
import { getMediaWorkflow, getCase, retryMediaWorkflow, sendCorrespondence, editCorrespondence } from '../lib/api'
import { reviewableCase } from '../lib/caseJourney.fixture'

vi.mock('../lib/api', () => ({ getMediaWorkflow: vi.fn(), getCase: vi.fn(), advanceMediaWorkflow: vi.fn(), retryMediaWorkflow: vi.fn(),
  editCorrespondence: vi.fn().mockResolvedValue({}), sendCorrespondence: vi.fn().mockResolvedValue({}) }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
const draft = { id: 'draft-1', kind: 'garage' as const, content_revision: 1, recipient: 'garage@example.fr',
  subject: 'Demande de devis', body: 'Bonjour, merci de proposer un devis détaillé.', source_url: null,
  version: 1, status: 'draft', error_code: null }

it('does not send on mount or with unsaved changes and requires explicit send selection', async () => {
  const saved = vi.fn()
  render(<CorrespondenceEditor draft={draft} currentRevision={1} accessToken="token" caseId="case-1" emailConfigured onSaved={saved} />)
  const send = screen.getByRole('button', { name: 'Envoyer l’email' }) as HTMLButtonElement
  expect(send.disabled).toBe(true)
  expect(sendCorrespondence).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('checkbox'))
  expect(send.disabled).toBe(false)
  fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'Message modifié' } })
  expect(send.disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le brouillon' }))
  await waitFor(() => expect(editCorrespondence).toHaveBeenCalled())
  expect(sendCorrespondence).not.toHaveBeenCalled()
})

it('sends precisely the reviewed draft version and does not enable stale or unconfigured drafts', async () => {
  const { rerender } = render(<CorrespondenceEditor draft={draft} currentRevision={1} accessToken="token" caseId="case-1" emailConfigured onSaved={vi.fn()} />)
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer l’email' }))
  await waitFor(() => expect(sendCorrespondence).toHaveBeenCalledWith('token', 'case-1', 'draft-1', 1))
  rerender(<CorrespondenceEditor draft={draft} currentRevision={2} accessToken="token" caseId="case-1" emailConfigured onSaved={vi.fn()} />)
  expect(screen.queryByRole('button', { name: 'Envoyer l’email' })).toBeNull()
})

it('renders exact insurer result as synthetic and a prepared garage draft', async () => {
  const caseView = reviewableCase()
  vi.mocked(getCase).mockResolvedValue(caseView)
  vi.mocked(getMediaWorkflow).mockResolvedValue({ content_revision: caseView.content_revision,
    email_configured: false, worker_configured: true, correspondence: [draft], workflow: {
      status: 'ready', error_code: null, updated_at: '2026-09-26T13:00:00Z', insurance_matches: [
        { vehicle: 'BMW sombre', plate: 'AB12 CDE', status: 'matched', reason: null,
          data: { insurer_name: 'Northbridge Demo Motor (fictional)' } },
      ],
    } })
  render(<MediaWorkflowPanel caseView={caseView} accessToken="token" onUpdated={vi.fn()} />)
  expect(await screen.findByText('Northbridge Demo Motor (fictional)')).toBeTruthy()
  expect(screen.getByText(/Associations fictives/)).toBeTruthy()
  expect(screen.getByText('Brouillon garage prêt')).toBeTruthy()
  expect(sendCorrespondence).not.toHaveBeenCalled()
})

it('retries a failed workflow only on request and preserves the intake while it is being edited', async () => {
  const caseView = reviewableCase()
  vi.mocked(getCase).mockResolvedValue(caseView)
  vi.mocked(getMediaWorkflow).mockResolvedValue({ content_revision: caseView.content_revision,
    email_configured: false, worker_configured: false, correspondence: [], workflow: {
      status: 'failed', error_code: 'gemini_rate_limited', updated_at: '2026-09-26T13:00:00Z', insurance_matches: [],
    } })
  const update = vi.fn()
  const { rerender } = render(<MediaWorkflowPanel caseView={caseView} accessToken="token" onUpdated={update} pauseUpdates />)
  const retry = await screen.findByRole('button', { name: 'Relancer le traitement' })
  expect(retryMediaWorkflow).not.toHaveBeenCalled()
  expect(update).not.toHaveBeenCalled()
  rerender(<MediaWorkflowPanel caseView={caseView} accessToken="token" onUpdated={update} />)
  await waitFor(() => expect(update).toHaveBeenCalledWith(caseView))
  fireEvent.click(retry)
  await waitFor(() => expect(retryMediaWorkflow).toHaveBeenCalledWith('token', caseView.id, caseView.state_version))
})
