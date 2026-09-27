// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import VoiceTestApp from './VoiceTestApp'

const mocks = vi.hoisted(() => ({
  getSession: vi.fn(), onAuthStateChange: vi.fn(),
  createSession: vi.fn(), getCall: vi.fn(), getCase: vi.fn(), getRecordingUrl: vi.fn(), updateIntake: vi.fn(),
  createEvidenceUploadIntent: vi.fn(), finalizeEvidence: vi.fn(),
  start: vi.fn(), stop: vi.fn(), getUserMedia: vi.fn(),
  listeners: {} as Record<string, (...args: unknown[]) => void>,
}))

vi.mock('./lib/supabase', () => ({
  authConfigured: true,
  supabase: { auth: { getSession: mocks.getSession, onAuthStateChange: mocks.onAuthStateChange,
    signInWithPassword: vi.fn() } },
}))
vi.mock('./lib/api', () => ({
  createVoiceWebTestSession: mocks.createSession,
  getVoiceWebTestCall: mocks.getCall,
  getCase: mocks.getCase,
  getVoiceRecordingReadUrl: mocks.getRecordingUrl,
  updateIntake: mocks.updateIntake,
  createEvidenceUploadIntent: mocks.createEvidenceUploadIntent,
  finalizeEvidence: mocks.finalizeEvidence,
}))
vi.mock('@vapi-ai/web', () => ({
  default: class FakeVapi {
    on(name: string, callback: (...args: unknown[]) => void) { mocks.listeners[name] = callback }
    removeAllListeners() { mocks.listeners = {} }
    start(id: string) { return mocks.start(id) }
    stop() { return mocks.stop() }
  },
}))

beforeEach(() => {
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: mocks.getUserMedia } })
  mocks.getUserMedia.mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] })
  mocks.getCase.mockResolvedValue({ id: 'case-1', state_version: 1, intake: { location: null, incident_at: null,
    injury_status: null, danger_status: null, policy_reference: null, narrative: 'Accident.' }, evidence: [],
    voice_session: {
      provider: 'vapi', session_id: 'web-call-1', mode: 'live', telephony_provider: 'web', status: 'incomplete',
      missing_p0: ['location', 'insured_name'],
      reason_codes: [], facts: [{ field: 'narrative', value: 'J’ai eu un accident.', excerpt: 'J’ai eu un accident.', uncertainty: 'explicit' }],
      segments: [
        { id: 'agent-1', speaker: 'assistant', text: 'Bonjour, comment puis-je vous aider ?', start_ms: 0, end_ms: 2000 },
        { id: 'user-1', speaker: 'caller', text: 'J’ai eu un accident.', start_ms: 2100, end_ms: 4000 },
        { id: 'agent-2', speaker: 'assistant', text: 'Où a eu lieu l’accident ?', start_ms: 4100, end_ms: 5500 },
      ],
      recording: { status: 'pending', mime_type: null, byte_size: null, sha256: null, error_code: null },
      call_started_at: '2026-09-26T08:00:00Z',
    } })
})
afterEach(() => { cleanup(); vi.clearAllMocks(); mocks.listeners = {} })

it('shows agent and caller turns live, then the full saved case', async () => {
  mocks.getSession.mockResolvedValue({ data: { session: { access_token: 'handler-jwt', user: { id: 'owner' } } } })
  mocks.onAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } })
  mocks.createSession.mockResolvedValue({ token: 'short-vapi-jwt', assistant_id: 'assistant-1',
    expires_at: new Date(Date.now() + 300_000).toISOString() })
  mocks.start.mockImplementation(async () => { mocks.listeners['call-start'](); return { id: 'web-call-1' } })
  mocks.stop.mockImplementation(async () => { mocks.listeners['call-end'](); })
  mocks.getCall.mockResolvedValue({ case_id: 'case-1', status: 'incomplete', recording_status: 'available' })

  render(<VoiceTestApp />)
  const start = await screen.findByRole('button', { name: 'Démarrer le test vocal' })
  await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false))
  expect(mocks.createSession).toHaveBeenCalledWith('handler-jwt')
  fireEvent.click(start)
  await waitFor(() => expect(mocks.getUserMedia).toHaveBeenCalledWith({ audio: true }))
  await waitFor(() => expect(mocks.start).toHaveBeenCalledWith('assistant-1'))
  mocks.listeners.message({ type: 'transcript', transcriptType: 'partial', role: 'user', transcript: 'bonjour...' })
  mocks.listeners.message({ type: 'conversation-update', messages: [
    { role: 'bot', message: 'Bonjour, comment puis-je vous aider ?' },
    { role: 'user', message: 'J’ai eu un accident.' },
    { role: 'bot', message: 'Où a eu lieu l’accident ?' },
  ] })
  const liveTranscript = await screen.findByRole('list', { name: 'Transcription en direct' })
  expect(liveTranscript.children).toHaveLength(3)
  expect(liveTranscript.children[0].textContent).toContain('Bonjour, comment puis-je vous aider ?')
  expect(liveTranscript.children[1].textContent).toContain('J’ai eu un accident.')
  expect(liveTranscript.children[2].textContent).toContain('Où a eu lieu l’accident ?')
  expect(screen.getByRole('row', { name: /Lieu de l’accident/ }).textContent).toContain('À confirmer')
  await waitFor(() => expect(screen.getByRole('row', { name: /Ce qui s’est passé/ }).textContent).toContain('Recueilli'))
  expect(screen.queryByText('bonjour...')).toBeNull()
  await waitFor(() => expect(screen.getByRole('link', { name: /Ouvrir le dossier/ }).getAttribute('href')).toBe('/cases/case-1'))
  fireEvent.click(screen.getByRole('button', { name: 'Terminer l’appel' }))
  await waitFor(() => expect(mocks.stop).toHaveBeenCalledTimes(1))
  const transcript = await screen.findByRole('list', { name: 'Transcription de l’appel' })
  expect(transcript.children).toHaveLength(3)
  expect(screen.getByText('Où a eu lieu l’accident ?')).toBeTruthy()
  expect(screen.getByRole('row', { name: /Lieu de l’accident/ }).textContent).toContain('À confirmer')
  expect(screen.getByRole('row', { name: /Ce qui s’est passé/ }).textContent).toContain('J’ai eu un accident.')
  await waitFor(() => expect(screen.getByText('Claimroom insurance')).toBeTruthy())
  expect(screen.getByText(/préciser le lieu/)).toBeTruthy()
})

it('rejects a denied microphone before creating a Vapi call', async () => {
  mocks.getSession.mockResolvedValue({ data: { session: { access_token: 'handler-jwt', user: { id: 'owner' } } } })
  mocks.onAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } })
  mocks.createSession.mockResolvedValue({ token: 'short-vapi-jwt', assistant_id: 'assistant-1',
    expires_at: new Date(Date.now() + 300_000).toISOString() })
  mocks.getUserMedia.mockRejectedValue(new DOMException('Permission denied', 'NotAllowedError'))

  render(<VoiceTestApp />)
  const start = await screen.findByRole('button', { name: 'Démarrer le test vocal' })
  await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(start)
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('micro'))
  expect(mocks.start).not.toHaveBeenCalled()
})
