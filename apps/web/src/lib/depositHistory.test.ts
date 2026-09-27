import { afterEach, expect, it, vi } from 'vitest'
import { HistoryWriter } from './depositHistory'
import type { ChatHistoryState } from './depositHistory'

const initial: ChatHistoryState = { phase: 'collect', opening: 'Bonjour', openingAt: '2026-09-27T10:00:00Z',
  messages: [], proposal: null }
const answer: ChatHistoryState = { ...initial, messages: [{ id: '1', role: 'insured', text: 'Alex Martin', created_at: '2026-09-27T10:01:00Z' }] }
const response = (revision: number) => ({ ok: true, status: 200, json: async () => ({ revision }) })
afterEach(() => vi.unstubAllGlobals())

it('serializes writes and retries the exact ambiguous checkpoint before newer turns', async () => {
  let finish: (value: unknown) => void = () => {}
  const first = new Promise(resolve => { finish = resolve })
  const fetch = vi.fn().mockReturnValueOnce(first).mockResolvedValueOnce(response(1)).mockResolvedValueOnce(response(2))
  vi.stubGlobal('fetch', fetch)
  const status = vi.fn()
  const writer = new HistoryWriter('session', 0, null, status)
  writer.enqueue(initial)
  writer.enqueue(answer)
  expect(fetch).toHaveBeenCalledTimes(1)
  finish({ ok: false, status: 503, json: async () => ({}) })
  await vi.waitFor(() => expect(status).toHaveBeenLastCalledWith(false, expect.any(String)))
  writer.retry()
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
  const requests = fetch.mock.calls.map(([, options]) => JSON.parse(options.body))
  expect(requests[0]).toEqual(requests[1])
  expect(requests[2]).toEqual({ expected_revision: 1, state: answer })
  await vi.waitFor(() => expect(status).toHaveBeenLastCalledWith(false, ''))
})

it('does not overwrite a conversation on a concurrent-session conflict', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: { code: 'stale_chat_history' } }) })
  vi.stubGlobal('fetch', fetch)
  const status = vi.fn()
  const writer = new HistoryWriter('session', 1, initial, status)
  writer.enqueue(answer)
  await vi.waitFor(() => expect(status).toHaveBeenLastCalledWith(false, expect.stringContaining('autre onglet')))
  writer.enqueue({ ...answer, phase: 'photos' })
  expect(fetch).toHaveBeenCalledTimes(1)
})
