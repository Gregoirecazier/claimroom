// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { CaseConversationPanel } from './CaseConversationPanel'
import type { CaseView } from '../lib/api'
import { saveConversation } from '../lib/conversation'
import { listPortalChat, listSmsLinks } from '../lib/api'

vi.mock('../lib/api', () => ({ listPortalChat: vi.fn(), listSmsLinks: vi.fn() }))

afterEach(() => { cleanup(); sessionStorage.clear(); vi.resetAllMocks() })

it('shows the saved chat with the attached photo and other case documents', () => {
  saveConversation('case-1', [
    { id: 1, side: 'agent', text: 'Pouvez-vous envoyer une photo ?', createdAt: '2026-09-26T10:00:00Z' },
    { id: 2, side: 'you', text: 'scene.png', createdAt: '2026-09-26T10:01:00Z', evidenceId: 'photo-1' },
  ])
  const photo = { id: 'photo-1', kind: 'scene_photo', mime_type: 'image/png', original_filename: 'scene.png', byte_size: 1200, received_at: '2026-09-26T10:01:00Z' }
  const pdf = { id: 'pdf-1', kind: 'document', mime_type: 'application/pdf', original_filename: 'constat.pdf', byte_size: 2500, received_at: '2026-09-26T10:02:00Z' }
  const onOpenEvidence = vi.fn()
  const { container } = render(<CaseConversationPanel caseView={{ id: 'case-1', evidence: [photo, pdf] } as CaseView}
    readUrls={{ 'photo-1': 'https://private.example/photo' }} onOpenEvidence={onOpenEvidence} />)
  expect(screen.getByText('Pouvez-vous envoyer une photo ?')).toBeTruthy()
  expect(container.querySelector('img')?.getAttribute('src')).toBe('https://private.example/photo')
  expect(screen.getByText('constat.pdf')).toBeTruthy()
  expect(screen.queryByText(/numéro de l’appelant/i)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Ouvrir constat.pdf' }))
  expect(onOpenEvidence).toHaveBeenCalledWith(pdf)
})

it('uses server history for an authenticated case without presenting browser simulation as real', async () => {
  saveConversation('case-1', [{ id: 1, side: 'agent', text: 'Ancienne simulation locale', createdAt: '2026-09-26T09:00:00Z' }])
  vi.mocked(listSmsLinks).mockResolvedValue([{ id: 'sms-1' }, { id: 'sms-2' }] as Awaited<ReturnType<typeof listSmsLinks>>)
  vi.mocked(listPortalChat).mockResolvedValue([{ id: 'server-message', sender: 'insured', body: 'Le lieu corrigé est bien celui-ci.', created_at: '2026-09-26T10:00:00Z' }])
  render(<CaseConversationPanel caseView={{ id: 'case-1', evidence: [], state_version: 2 } as unknown as CaseView}
    accessToken="private-session" readUrls={{}} onOpenEvidence={vi.fn()} />)
  expect(await screen.findByText('Le lieu corrigé est bien celui-ci.')).toBeTruthy()
  expect(screen.getAllByText('Le lieu corrigé est bien celui-ci.')).toHaveLength(1)
  expect(screen.queryByText('Ancienne simulation locale')).toBeNull()
  expect(listPortalChat).toHaveBeenCalledWith('private-session', 'case-1', 'sms-1')
})

it('shows unavailable history as an error rather than claiming there were no exchanges', async () => {
  vi.mocked(listSmsLinks).mockRejectedValue(new Error('offline'))
  render(<CaseConversationPanel caseView={{ id: 'case-1', evidence: [], state_version: 2 } as unknown as CaseView}
    accessToken="private-session" readUrls={{}} onOpenEvidence={vi.fn()} />)
  expect((await screen.findByRole('alert')).textContent).toContain('historique est momentanément indisponible')
  expect(screen.queryByText('Aucun échange ni pièce reçue pour le moment.')).toBeNull()
})
