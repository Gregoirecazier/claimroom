// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { Evidence } from '../lib/api'
import { EvidencePreview } from './EvidencePreview'

const evidence = {
  id: 'evidence-1', kind: 'scene_video', role: null, mime_type: 'video/mp4',
} as Evidence

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

it('renews a private video URL before expiry while the player remains open', async () => {
  const renew = vi.fn().mockResolvedValue({ evidence_id: evidence.id, url: 'https://private/new', expires_at: '2026-01-01T00:10:00Z' })
  const view = render(<EvidencePreview evidence={evidence} initial={{ evidence_id: evidence.id, url: 'https://private/old', expires_at: '2026-01-01T00:05:00Z' }} renew={renew} onClose={() => {}} />)
  expect(view.container.querySelector('video')?.src).toBe('https://private/old')
  await act(async () => { vi.advanceTimersByTime(270_000); await Promise.resolve() })
  expect(renew).toHaveBeenCalledOnce()
  expect(view.container.querySelector('video')?.src).toBe('https://private/new')
})

it('shows a Storage error and lets the user retry a failed renewal', async () => {
  const renew = vi.fn().mockRejectedValueOnce(new Error('Storage unavailable'))
    .mockResolvedValueOnce({ evidence_id: evidence.id, url: 'https://private/recovered', expires_at: '2026-01-01T00:10:00Z' })
  const view = render(<EvidencePreview evidence={evidence} initial={{ evidence_id: evidence.id, url: 'https://private/old', expires_at: '2026-01-01T00:05:00Z' }} renew={renew} onClose={() => {}} />)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Renouveler le lien' })); await Promise.resolve() })
  expect(screen.getByRole('alert').textContent).toContain('indisponible')
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Renouveler le lien' })); await Promise.resolve() })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(view.container.querySelector('video')?.src).toBe('https://private/recovered')
})
