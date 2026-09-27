// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SmsDepositPanel } from './SmsDepositPanel'
import type { CaseView } from '../lib/api'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const caseView = { id: 'case-1', state_version: 4 } as CaseView
const message = (id: string, status: string, created_at: string) => ({ id, status, created_at })
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })

it('shows one direct link to the latest valid private SMS chat', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url.endsWith('/sms-links')) return response([
      message('older', 'delivered', '2026-09-25T12:00:00Z'),
      message('failed', 'failed', '2026-09-26T17:00:00Z'),
      message('newer', 'delivered', '2026-09-26T16:26:00Z'),
    ])
    if (url.endsWith('/newer/link')) return response({ error: { code: 'deposit_link_expired' } }, 409)
    if (url.endsWith('/older/link')) return response({ url: 'https://claimroom.example/depot#token=older-token' })
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetch)
  render(<SmsDepositPanel caseView={caseView} accessToken="manager-token" />)

  const link = await screen.findByRole('link', { name: 'Ouvrir le chat privé' })
  expect(link.getAttribute('href')).toBe('https://claimroom.example/depot#token=older-token')
  expect(link.getAttribute('target')).toBe('_blank')
  expect(link.getAttribute('referrerpolicy')).toBe('no-referrer')
  expect(screen.queryByText('Préparer le SMS')).toBeNull()
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    expect.stringContaining('/sms-links'),
    expect.stringContaining('/newer/link'),
    expect.stringContaining('/older/link'),
  ])
})
