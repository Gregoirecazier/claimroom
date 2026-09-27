// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import G1App from './G1App'
import { deleteCase, listCasesPage } from './lib/api'
import type { CaseSummary } from './lib/api'

vi.mock('./lib/supabase', () => ({
  authConfigured: true,
  supabase: { auth: {
    getSession: async () => ({ data: { session: { access_token: 'test-token', user: { email: 'test@example.test' } } } }),
    onAuthStateChange: () => ({ data: { subscription: { unsubscribe: vi.fn() } } }),
  } },
}))
vi.mock('./lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('./lib/api')>(),
  getCurrentUser: vi.fn().mockResolvedValue({ id: 'owner' }),
  listCasesPage: vi.fn(),
  deleteCase: vi.fn(),
}))

const cases = ['DOSSIER-A', 'DOSSIER-B'].map((reference, index) => ({
  id: `case-${index}`, scenario_id: 'g1', status: 'collecting',
  created_at: '2026-09-27T12:00:00Z', intake: { insured_reference: reference },
})) as CaseSummary[]

beforeEach(() => {
  window.history.replaceState(null, '', '/')
  window.scrollTo = vi.fn()
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: vi.fn().mockReturnValue({ matches: false }) })
  vi.mocked(listCasesPage).mockResolvedValue({ items: cases, total: cases.length, has_more: false, offset: 0, limit: 20 })
  vi.mocked(deleteCase).mockResolvedValue(undefined)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); window.history.replaceState(null, '', '/') })

it('confirms deletion, prevents duplicate submissions, and updates the list and count', async () => {
  let finish!: () => void
  vi.mocked(deleteCase).mockImplementation(() => new Promise<void>(resolve => { finish = resolve }))
  render(<G1App />)
  const button = await screen.findByRole('button', { name: /Supprimer le dossier DOSSIER-A/ })
  fireEvent.click(button)
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('DOSSIER-A'))
  expect(deleteCase).toHaveBeenCalledWith('test-token', 'case-0')
  expect(button.hasAttribute('disabled')).toBe(true)
  expect(screen.getByText('Suppression…')).toBeTruthy()
  fireEvent.click(button)
  expect(deleteCase).toHaveBeenCalledTimes(1)
  finish()
  await waitFor(() => expect(screen.queryByRole('link', { name: 'DOSSIER-A' })).toBeNull())
  expect(screen.getByRole('link', { name: 'DOSSIER-B' })).toBeTruthy()
  expect(screen.getByRole('link', { name: /Tous les dossiers 1/ })).toBeTruthy()
  expect(screen.getByRole('status').textContent).toContain('a été supprimé')
})

it('keeps the dossier when confirmation is cancelled', async () => {
  vi.mocked(window.confirm).mockReturnValue(false)
  render(<G1App />)
  fireEvent.click(await screen.findByRole('button', { name: /Supprimer le dossier DOSSIER-A/ }))
  expect(deleteCase).not.toHaveBeenCalled()
  expect(screen.getByRole('link', { name: 'DOSSIER-A' })).toBeTruthy()
})

it('keeps the dossier and allows retry when deletion fails', async () => {
  vi.mocked(deleteCase).mockRejectedValueOnce(new Error('Service indisponible'))
  render(<G1App />)
  const button = await screen.findByRole('button', { name: /Supprimer le dossier DOSSIER-A/ })
  fireEvent.click(button)
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Impossible de supprimer'))
  expect(button.hasAttribute('disabled')).toBe(false)
  expect(screen.getByRole('link', { name: 'DOSSIER-A' })).toBeTruthy()
  fireEvent.click(button)
  await waitFor(() => expect(screen.queryByRole('link', { name: 'DOSSIER-A' })).toBeNull())
})
