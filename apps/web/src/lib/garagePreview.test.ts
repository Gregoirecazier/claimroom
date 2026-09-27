import { afterEach, expect, it, vi } from 'vitest'
import { previewGarages } from './api'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('aborts a stalled garage request after 25 seconds', async () => {
  vi.useFakeTimers()
  let signal: AbortSignal | undefined
  vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    signal = init.signal as AbortSignal
    signal.addEventListener('abort', () => reject(new DOMException('Timed out', 'AbortError')))
  })))
  const pending = previewGarages('test', 'case-id', 1, null)
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  await vi.advanceTimersByTimeAsync(25_000)
  await rejected
  expect(signal?.aborted).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
})

it('clears the deadline once a garage request succeeds', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ready', garages: [] }) }))
  expect(await previewGarages('test', 'case-id', 1, null)).toEqual({ status: 'ready', garages: [] })
  expect(vi.getTimerCount()).toBe(0)
})
