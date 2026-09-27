// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { FollowUpSmsPanel } from './FollowUpSmsPanel'
import type { CaseView } from '../lib/api'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const caseView = {
  id: '00000000-0000-4000-8000-000000000001', state_version: 4, content_revision: 2,
  voice_session: { mode: 'live', telephony_provider: 'twilio', status: 'incomplete' },
} as CaseView

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

it('creates the fake message without a preview or recipient form and exposes its phone link', async () => {
  const fetch = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url.endsWith('/messages/auto-fake')) return response({ id: 'sms1', status: 'delivered' })
    if (url.endsWith('/messages/sms1/link')) return response({
      url: 'https://depot.example/#token=secret', expires_at: '2099-09-25T12:00:00Z',
    })
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetch)
  const onUpdated = vi.fn(async () => {})
  render(<FollowUpSmsPanel caseView={caseView} accessToken="manager-token" onUpdated={onUpdated} />)
  const link = await screen.findByRole('link', { name: 'Ouvrir la conversation WhatsApp' })
  expect(link.getAttribute('href')).toBe('/fake-whatsapp#token=secret')
  expect(document.body.textContent).not.toContain('Confirmer le numéro')
  expect(document.body.textContent).not.toContain('Aperçu')
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    expect.stringContaining('/messages/auto-fake'),
    expect.stringContaining('/messages/sms1/link'),
  ])
  expect(fetch.mock.calls[0][1]?.method).toBe('POST')
  await waitFor(() => expect(onUpdated).toHaveBeenCalledOnce())
})

it('shows no follow-up when fake WhatsApp is not configured', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response({ error: { code: 'fake_whatsapp_not_configured', message: 'Unavailable' } }, 404)))
  render(<FollowUpSmsPanel caseView={caseView} accessToken="manager-token" onUpdated={async () => {}} />)
  await waitFor(() => expect(screen.queryByRole('region', { name: 'Téléphone WhatsApp simulé' })).toBeNull())
})
