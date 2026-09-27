// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import G1App from './G1App'
import { updateIntake } from './lib/api'

vi.mock('./lib/supabase', () => ({ authConfigured: true, supabase: { auth: {
  getSession: async () => ({ data: { session: { access_token: 'test', user: { email: 'manager@example.test' } } } }),
  onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }), signOut: vi.fn(),
} } }))
vi.mock('./lib/api', async () => {
  const { reviewableCase } = await import('./lib/caseJourney.fixture')
  return {
    getAccidentJourney: vi.fn().mockResolvedValue({ content_revision: 2, review: null, notifications: [], catalogue_count: 3 }),
    getMediaWorkflow: vi.fn().mockResolvedValue({ workflow: null }),
    getGarageSms: vi.fn().mockResolvedValue({ settings: null, jobs: [], mode: 'mock', trigger: 'photos' }),
    advanceMediaWorkflow: vi.fn(), retryMediaWorkflow: vi.fn(), approveAccident: vi.fn(), accidentVideo: vi.fn(),
    ApiError: class extends Error {}, getCurrentUser: vi.fn().mockResolvedValue({}),
    getCase: vi.fn().mockImplementation(async () => reviewableCase()),
    listCasesPage: vi.fn().mockImplementation(async () => ({ items: [reviewableCase()], total: 1, offset: 0, limit: 20, has_more: false })),
    listSmsLinks: vi.fn().mockResolvedValue([]), listPortalChat: vi.fn().mockResolvedValue([]),
    getEvidenceReadUrl: vi.fn().mockResolvedValue({ url: '/photo.png' }),
    getTransmissionPreview: vi.fn().mockResolvedValue(null),
    createEvidenceUploadIntent: vi.fn(), finalizeEvidence: vi.fn(), runAnalysis: vi.fn(),
    runVideoAnalysis: vi.fn(), runMediaAnalysis: vi.fn(), seedG1Media: vi.fn(), updateIntake: vi.fn(),
    approveDraft: vi.fn(), downloadTransmissionReceipt: vi.fn(), simulateRegistration: vi.fn(),
    simulateSend: vi.fn(), updateCurrentDraft: vi.fn(),
  }
})
vi.mock('./components/VoiceIntakePanel', () => ({ VoiceIntakePanel: () => null }))
vi.mock('./components/FollowUpSmsPanel', () => ({ FollowUpSmsPanel: () => null }))
vi.mock('./components/CamerasPanel', () => ({ CamerasPanel: () => null }))
vi.mock('./components/PhotoLookupPanel', () => ({ PhotoLookupPanel: () => null }))
vi.mock('./components/CameraMailPanel', () => ({ CameraMailPanel: () => null }))
vi.mock('./components/MediaWorkflowPanel', () => ({ MediaWorkflowPanel: () => null }))
vi.mock('./components/VisionPanel', () => ({ VisionPanel: () => null }))
vi.mock('./components/CaseReport', () => ({ CaseReport: () => <section id="sourced-report">Compte rendu de test</section> }))
vi.mock('./components/EstimatePanel', () => ({ EstimatePanel: () => <section id="estimate">Estimation de test</section> }))

const path = '/cases/00000000-0000-4000-8000-000000000001'
beforeEach(() => {
  window.history.replaceState(null, '', path)
  Element.prototype.scrollIntoView = vi.fn()
  window.scrollTo = vi.fn()
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: vi.fn().mockReturnValue({ matches: false }) })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
async function navigate(hash: string) {
  await act(async () => {
    window.history.pushState(null, '', `${path}${hash}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

it('opens the automatic journey directly and removes obsolete manual steps', async () => {
  window.history.replaceState(null, '', `${path}#review`)
  render(<G1App />)
  expect(await screen.findByRole('heading', { name: 'Analyse et décision' })).toBeTruthy()
  const stages = screen.getByRole('navigation', { name: 'Étapes du dossier' })
  expect(stages.querySelectorAll('a')).toHaveLength(3)
  expect(stages.querySelector('a[href="#analysis"]')?.getAttribute('aria-current')).toBe('step')
  expect(screen.queryByRole('link', { name: 'Notifications' })).toBeNull()
  expect(screen.queryByRole('link', { name: 'Validation' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Lancer l’analyse' })).toBeNull()
  expect(screen.queryByRole('heading', { name: 'Assureurs et demande de devis' })).toBeNull()
  await navigate('#evidence')
  expect(screen.queryByRole('heading', { name: 'Analyse et décision' })).toBeNull()
  await navigate('#review')
  expect(screen.getByRole('heading', { name: 'Analyse et décision' })).toBeTruthy()
})

it('preserves an intake edit during hash navigation without submitting it', async () => {
  window.history.replaceState(null, '', `${path}#report`)
  render(<G1App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Modifier les informations' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Récit' }), { target: { value: 'Récit en cours de correction' } })
  await navigate('#analysis')
  await waitFor(() => expect(screen.getByRole('heading', { name: 'Analyse et décision' })).toBeTruthy())
  expect(screen.queryByRole('textbox', { name: 'Récit' })).toBeNull()
  await navigate('#report')
  expect((screen.getByRole('textbox', { name: 'Récit' }) as HTMLTextAreaElement).value).toBe('Récit en cours de correction')
  expect(updateIntake).not.toHaveBeenCalled()
})

it('keeps the case title and badges with stage navigation while scrolling between steps', async () => {
  render(<G1App />)
  const stages = await screen.findByRole('navigation', { name: 'Étapes du dossier' })
  const stageLink = (hash: string) => stages.querySelector<HTMLAnchorElement>(`a[href="${hash}"]`)!
  const stickyHeader = stages.closest('.cr-sticky-case-header')
  expect(stickyHeader?.querySelector('h1')?.textContent).toBe('Peugeot grise')
  expect(stickyHeader?.querySelector('.cr-case-badges')?.textContent).toContain('FR-482-KL')
  expect(screen.queryByRole('navigation', { name: 'Historique de navigation' })).toBeNull()
  fireEvent.click(stageLink('#report'))
  expect(stageLink('#report').getAttribute('aria-current')).toBe('step')
  expect(screen.getByRole('heading', { name: 'Les faits connus' })).toBeTruthy()
  await waitFor(() => expect(window.scrollTo).toHaveBeenLastCalledWith({ top: expect.any(Number), behavior: 'smooth' }))
  fireEvent.click(stageLink('#analysis'))
  await navigate('#report')
  await waitFor(() => expect(stageLink('#report').getAttribute('aria-current')).toBe('step'))
  await navigate('#analysis')
  await waitFor(() => expect(stageLink('#analysis').getAttribute('aria-current')).toBe('step'))
  fireEvent.click(stageLink('#evidence'))
  expect(stageLink('#evidence').getAttribute('aria-current')).toBe('step')
})

it('respects reduced motion and keeps same-section deep links navigable', async () => {
  vi.mocked(window.matchMedia).mockReturnValue({ matches: true } as MediaQueryList)
  render(<G1App />)
  const stages = await screen.findByRole('navigation', { name: 'Étapes du dossier' })
  fireEvent.click(stages.querySelector('a[href="#analysis"]')!)
  await waitFor(() => expect(window.location.hash).toBe('#analysis'))
  await waitFor(() => expect(window.scrollTo).toHaveBeenLastCalledWith({ top: expect.any(Number), behavior: 'instant' }))
  await navigate('#review')
  expect(stages.querySelector('a[href="#analysis"]')?.getAttribute('aria-current')).toBe('step')
  await navigate('#transmission')
  expect(stages.querySelector('a[href="#analysis"]')?.getAttribute('aria-current')).toBe('step')
  expect(screen.getByRole('heading', { name: 'Analyse et décision' })).toBeTruthy()
})
