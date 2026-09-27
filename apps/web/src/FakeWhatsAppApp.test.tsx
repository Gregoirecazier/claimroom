// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import FakeWhatsAppApp from './FakeWhatsAppApp'

const mocks = vi.hoisted(() => ({ uploadToSignedUrl: vi.fn() }))
vi.mock('./lib/supabase', () => ({
  supabase: { storage: { from: () => ({ uploadToSignedUrl: mocks.uploadToSignedUrl }) } },
}))

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks() })

it('opens the sent-number inbox, saves a text reply, and links an uploaded file to evidence', async () => {
  window.history.replaceState(null, '', '/fake-whatsapp#token=private-link')
  const outbound = { id: 'sent-1', side: 'agent', text: 'Bonjour https://claim.example/depot#token=private-link',
    created_at: '2026-09-26T12:00:00Z', evidence_id: null, filename: null }
  const textReply = { id: 'reply-1', side: 'you', text: 'Bonjour', created_at: '2026-09-26T12:01:00Z', evidence_id: null, filename: null }
  const attachment = { id: 'reply-2', side: 'you', text: '', created_at: '2026-09-26T12:02:00Z', evidence_id: 'evidence-1', filename: 'photo.png' }
  let replies = 0
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const body = (() => {
      if (url.endsWith('/v1/deposit/session')) return { session_token: 'guest-session' }
      if (url.endsWith('/v1/fake-whatsapp/conversation')) return { case_id: 'case-1', number: '+33612345678',
        deposit_url: 'https://claim.example/depot#token=private-link', messages: [outbound, ...(replies > 0 ? [textReply] : []), ...(replies > 1 ? [attachment] : [])] }
      if (url.endsWith('/v1/fake-whatsapp/messages') && init?.method === 'POST') {
        replies += 1
        return { case_id: 'case-1', number: '+33612345678', deposit_url: 'https://claim.example/depot#token=private-link',
          messages: [outbound, textReply, ...(replies > 1 ? [attachment] : [])] }
      }
      if (url.endsWith('/v1/deposit/summary')) return { state_version: 2, evidence: [] }
      if (url.endsWith('/v1/deposit/evidence/upload-intents')) return { bucket: 'private', storage_path: 'case-1/photo.png', token: 'signed-upload' }
      if (url.endsWith('/v1/deposit/evidence')) return { state_version: 3, evidence: [{ id: 'evidence-1' }] }
      throw new Error(`Unexpected request ${url}`)
    })()
    return { ok: true, json: async () => body }
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValue('client-id'),
    subtle: { digest: vi.fn().mockResolvedValue(new Uint8Array(32).buffer) } })
  mocks.uploadToSignedUrl.mockResolvedValue({ error: null })

  render(<FakeWhatsAppApp />)
  await screen.findByText('+33612345678')
  expect(window.location.hash).toBe('')
  expect(screen.getByRole('link', { name: 'Ouvrir le lien de dépôt sécurisé' })).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Votre message'), { target: { value: 'Bonjour' } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await screen.findByText('Bonjour')
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/v1/fake-whatsapp/messages'),
    expect.objectContaining({ method: 'POST' }))

  const photo = new File(['picture'], 'photo.png', { type: 'image/png' })
  Object.defineProperty(photo, 'arrayBuffer', { value: async () => new TextEncoder().encode('picture').buffer })
  fireEvent.change(screen.getByLabelText('Fichier'), { target: { files: [photo] } })
  fireEvent.click(screen.getByRole('button', { name: 'Joindre au dossier' }))
  await screen.findByText('📎 photo.png')
  await waitFor(() => expect(mocks.uploadToSignedUrl).toHaveBeenCalledWith(
    'case-1/photo.png', 'signed-upload', photo, expect.objectContaining({ contentType: 'image/png' })))
  expect(replies).toBe(2)
})
