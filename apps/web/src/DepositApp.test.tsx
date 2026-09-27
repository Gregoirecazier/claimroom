// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import DepositApp from './DepositApp'

const { uploadToSignedUrl } = vi.hoisted(() => ({ uploadToSignedUrl: vi.fn() }))
vi.mock('./lib/supabase', () => ({
  supabase: { storage: { from: () => ({ uploadToSignedUrl }) } },
}))

const initial = {
  case_id: 'case-1', state_version: 1, content_revision: 1,
  source_label: 'Selon votre déclaration',
  intake: { insured_name: 'Camille Martin' as string | null, incident_at: null as string | null, insured_reference: 'REF-42', narrative: 'Accrochage au rond-point', location: null, injury_status: null },
  missing_fields: ['insured_reference', 'incident_at', 'location', 'danger_status', 'injury_status'],
  transcript_available: true, recording_status: 'available', evidence: [], conversation_available: true,
}
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })

let storedHistory: { revision: number; state: unknown } = { revision: 0, state: null }

function withHistory(fetch: (url: string, options?: RequestInit) => unknown) {
  return (url: string, options?: RequestInit) => {
    if (url.endsWith('/chat-history')) {
      if (options?.method === 'PUT') {
        const body = JSON.parse(String(options.body))
        storedHistory = { revision: storedHistory.revision + 1, state: body.state }
      }
      return Promise.resolve(response(storedHistory))
    }
    return fetch(url, options)
  }
}

function openChat(fetch: (url: string, options?: RequestInit) => unknown) {
  window.history.replaceState(null, '', '/depot#token=private-link')
  vi.stubGlobal('fetch', withHistory(fetch))
  render(<DepositApp />)
}

afterEach(() => {
  cleanup()
  storedHistory = { revision: 0, state: null }
  sessionStorage.clear()
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
})

it('starts with a factual recap and asks for confirmation before any missing detail', async () => {
  const fetch = vi.fn(async (url: string, _options?: RequestInit) => response(url.endsWith('/session')
    ? { session_token: 'guest-session' } : initial))
  openChat(fetch)
  const greeting = await screen.findByText(/Bonjour, voici le récapitulatif de votre dossier/)
  expect(greeting.textContent).toContain('REF-42')
  expect(greeting.textContent).toContain('Accrochage au rond-point')
  expect(greeting.textContent).toContain('Pouvez-vous confirmer que ces informations sont exactes ?')
  expect(screen.queryByText(/Où l’accident a-t-il eu lieu/)).toBeNull()
  expect(screen.queryByRole('textbox', { name: 'Votre message' })).toBeNull()
  expect(screen.getByText('Assistant')).toBeTruthy()
  expect(screen.queryByText('Gestionnaire')).toBeNull()
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/conversation'))).toBe(false)
  expect(window.location.hash).toBe('')
  expect((fetch.mock.calls[0]?.[1]?.headers as Record<string, string>).Authorization).toBe('Bearer private-link')
})

it('asks only for the missing date/time, then requests photos after confirmation', async () => {
  const updated = { ...initial, state_version: 2, content_revision: 2,
    intake: { ...initial.intake, incident_at: '2026-09-26T10:00:00+02:00' },
    missing_fields: ['insured_reference', 'location', 'danger_status', 'injury_status'],
    analysis_requests: ['Votre véhicule était-il arrêté ?'] }
  const fetch = vi.fn(async (url: string, _options?: RequestInit) => {
    if (url.endsWith('/session')) return response({ session_token: 'guest-session' })
    if (url.endsWith('/conversation')) return response({ id: 'message' })
    if (url.endsWith('/chat')) return response({ reply: 'À confirmer',
      proposal: { field: 'incident_at', value: updated.intake.incident_at } })
    if (url.endsWith('/corrections')) return response(updated)
    return response(initial)
  })
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  expect(screen.getByText(/À quelle date et à quelle heure/)).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: 'Votre message' }), { target: { value: 'Le 26 septembre 2026 à 10h' } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await screen.findByText(/J’ai noté Date et heure/)
  const chat = fetch.mock.calls.find(([url]) => url.endsWith('/chat'))
  expect(JSON.parse(String(chat![1]?.body)).target_field).toBe('incident_at')
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/corrections'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Confirmer' }))
  await screen.findByText(/Correction enregistrée. Vous pouvez ajouter vos photos/)
  expect(screen.queryByText(/photos ont bien été reçues/)).toBeNull()
  expect(screen.queryByText(/Quelle est votre référence|Où l’accident|Y a-t-il des blessés|encore un danger|Votre véhicule était-il arrêté/)).toBeNull()
  expect(screen.getByRole('textbox', { name: 'Votre message' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Ajouter une pièce jointe' })).toBeTruthy()
})

it('requests photos directly when the date/time is already known despite other missing fields', async () => {
  const complete = { ...initial, intake: { ...initial.intake, incident_at: '2026-09-26T10:00:00+02:00' } }
  const fetch = vi.fn(async (url: string) => response(url.endsWith('/session')
    ? { session_token: 'guest-session' } : complete))
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  expect(screen.getByText(/Pouvez-vous joindre des photos/)).toBeTruthy()
  expect(screen.queryByText(/À quelle date et à quelle heure/)).toBeNull()
})

it('acknowledges the corrected field without repeating the recap, then begins missing questions', async () => {
  const corrected = { ...initial, state_version: 2,
    intake: { ...initial.intake, insured_reference: 'REF-43' } }
  const fetch = vi.fn(async (url: string, _options?: RequestInit) => {
    if (url.endsWith('/session')) return response({ session_token: 'guest-session' })
    if (url.endsWith('/conversation')) return response({ id: 'message' })
    if (url.endsWith('/chat')) return response({ reply: 'À confirmer', proposal: { field: 'insured_reference', value: 'REF-43' } })
    if (url.endsWith('/corrections')) return response(corrected)
    return response(initial)
  })
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Corriger une information' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Votre message' }), { target: { value: 'Ma référence est REF-43' } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await screen.findByText(/J’ai noté Référence du contrat : REF-43/)
  const chatBody = JSON.parse(String(fetch.mock.calls.find(([url]) => url.endsWith('/chat'))![1]?.body))
  expect(chatBody.target_field).toBeUndefined()
  fireEvent.click(screen.getByRole('button', { name: 'Confirmer' }))
  await screen.findByText(/Correction enregistrée/ )
  expect(screen.getAllByText(/Pouvez-vous confirmer que ces informations sont exactes/)).toHaveLength(1)
  expect(screen.queryByText(/Où l’accident a-t-il eu lieu/)).toBeNull()
  expect(screen.getByText(/À quelle date et à quelle heure/)).toBeTruthy()
})

it('offers a correction fallback if the assistant is unavailable', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url.endsWith('/session')) return response({ session_token: 'guest-session' })
    if (url.endsWith('/conversation')) return response({ id: 'message' })
    if (url.endsWith('/chat')) return response({ detail: { code: 'chat_unavailable' } }, 503)
    return response(initial)
  })
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Votre message' }), { target: { value: 'Paris' } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await screen.findByText(/Je ne peux pas interpréter votre réponse/)
  expect(screen.getByText('Modifier directement une information')).toBeTruthy()
})

it('keeps file attachment available during collection and rejects oversized files', async () => {
  const fetch = vi.fn(async (url: string) => response(url.endsWith('/session')
    ? { session_token: 'guest-session' } : initial))
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  const input = screen.getByLabelText('Choisir une pièce jointe') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File(['photo'], 'photo.jpg', { type: 'image/jpeg' })] } })
  expect(screen.getByText('photo.jpg')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Retirer photo.jpg' }))
  expect(screen.queryByText('photo.jpg')).toBeNull()
  fireEvent.change(input, { target: { files: [new File([new Uint8Array(5_000_001)], 'large.jpg', { type: 'image/jpeg' })] } })
  expect(screen.getByRole('alert').textContent).toContain('5 Mo maximum')
})


it('keeps collection open until the insured explicitly finishes adding photos', async () => {
  const complete = { ...initial, intake: { ...initial.intake, incident_at: '2026-09-26T10:00:00+02:00' } }
  const photo = { id: 'photo-1', kind: 'scene_photo', source_kind: 'insured_upload', mime_type: 'image/jpeg',
    byte_size: 5, checksum_status: 'verified', original_filename: 'photo.jpg', received_at: new Date().toISOString() }
  let current = { ...complete, evidence: [] as typeof photo[], analysis_requests: [] as string[] }
  const fetch = vi.fn(async (url: string) => response(url.endsWith('/session')
    ? { session_token: 'guest-session' } : current))
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  expect(screen.getByText(/Pouvez-vous joindre des photos/)).toBeTruthy()
  current = { ...current, state_version: 2, content_revision: 2, evidence: [photo],
    analysis_requests: ['Votre véhicule était-il arrêté avant le choc ?'] }
  await act(async () => { fireEvent(window, new Event('focus')) })
  await screen.findByText('photo.jpg')
  expect(screen.queryByText(/Votre ajout de pièces est terminé/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Terminer l’ajout' }))
  await screen.findByText(/Votre ajout de pièces est terminé/)
  expect(screen.queryByLabelText('Compléments pour l’analyse')).toBeNull()
  expect(screen.queryByText(/Votre véhicule était-il arrêté/)).toBeNull()
  expect(screen.getByRole('textbox', { name: 'Votre message' })).toBeTruthy()
  await act(async () => { fireEvent(window, new Event('focus')) })
  expect(screen.getAllByText(/Votre ajout de pièces est terminé/)).toHaveLength(1)
  expect(screen.getByRole('button', { name: 'Ajouter une pièce jointe' })).toBeTruthy()
})

it('does not treat a PDF as a photo', async () => {
  const current = { ...initial, intake: { ...initial.intake, incident_at: '2026-09-26T10:00:00+02:00' },
    evidence: [{ id: 'pdf-1', kind: 'document', source_kind: 'insured_upload', mime_type: 'application/pdf',
      byte_size: 5, checksum_status: 'verified', original_filename: 'constat.pdf', received_at: new Date().toISOString() }] }
  const fetch = vi.fn(async (url: string) => response(url.endsWith('/session')
    ? { session_token: 'guest-session' } : current))
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  expect(screen.getByText(/Pouvez-vous joindre des photos/)).toBeTruthy()
  expect(screen.queryByText(/vos photos ont bien été reçues/)).toBeNull()
})

it('uploads a photo from the photo step and acknowledges it only after finalization', async () => {
  const complete = { ...initial, intake: { ...initial.intake, incident_at: '2026-09-26T10:00:00+02:00' } }
  const photo = { id: 'photo-1', kind: 'scene_photo', source_kind: 'insured_upload', mime_type: 'image/jpeg',
    byte_size: 5, checksum_status: 'verified', original_filename: 'photo.jpg', received_at: new Date().toISOString() }
  let finish: (value: ReturnType<typeof response>) => void = () => {}
  const finalized = new Promise<ReturnType<typeof response>>(resolve => { finish = resolve })
  uploadToSignedUrl.mockResolvedValue({ error: null })
  vi.stubGlobal('crypto', { randomUUID: crypto.randomUUID.bind(crypto),
    subtle: { digest: async () => new Uint8Array(32).buffer } })
  const fetch = vi.fn(async (url: string, _options?: RequestInit) => {
    if (url.endsWith('/session')) return response({ session_token: 'guest-session' })
    if (url.endsWith('/upload-intents')) return response({ bucket: 'evidence', storage_path: 'case/photo.jpg', token: 'upload' })
    if (url.endsWith('/evidence')) return finalized
    return response(complete)
  })
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  const file = new File(['photo'], 'photo.jpg', { type: 'image/jpeg' })
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new Uint8Array(5).buffer })
  fireEvent.change(screen.getByLabelText('Choisir une pièce jointe'), { target: { files: [file] } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith('/evidence'))).toBe(true))
  expect(screen.queryByText(/vos photos ont bien été reçues/)).toBeNull()
  await act(async () => { finish(response({ ...complete, state_version: 2, content_revision: 2, evidence: [photo] })) })
  await screen.findByText(/1 pièce reçue/)
  expect(screen.queryByText(/Votre ajout de pièces est terminé/)).toBeNull()
  expect(uploadToSignedUrl).toHaveBeenCalled()
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/chat'))).toBe(false)
  expect(screen.getByRole('button', { name: 'Ajouter une pièce jointe' })).toBeTruthy()
})

it('shows the date/time clarification instead of repeating the whole question', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url.endsWith('/session')) return response({ session_token: 'guest-session' })
    if (url.endsWith('/conversation')) return response({ id: 'message' })
    if (url.endsWith('/chat')) return response({ reply: 'À quelle heure environ ?', proposal: null })
    return response(initial)
  })
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Votre message' }), { target: { value: 'Le 26 septembre' } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await screen.findByText('À quelle heure environ ?')
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/corrections'))).toBe(false)
})

it('restores the thread, finished phase and draft after refresh without retaining the raw grant', async () => {
  const current = { ...initial, intake: { ...initial.intake, incident_at: '2026-09-26T10:00:00+02:00' },
    evidence: [{ id: 'photo-1', kind: 'scene_photo', source_kind: 'insured_upload', mime_type: 'image/jpeg',
      byte_size: 5, checksum_status: 'verified', original_filename: 'photo.jpg', received_at: new Date().toISOString() }] }
  const fetch = vi.fn(async (url: string) => response(url.endsWith('/session')
    ? { session_token: 'guest-session' } : current))
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  fireEvent.click(screen.getByRole('button', { name: 'Terminer l’ajout' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Votre message' }), { target: { value: 'Une précision à ajouter' } })
  expect(sessionStorage.getItem('claimroom.deposit.session.v1')).not.toContain('private-link')
  cleanup()
  render(<DepositApp />)
  await screen.findByText(/Votre ajout de pièces est terminé/)
  expect(screen.getAllByText(/Bonjour, voici le récapitulatif/)).toHaveLength(1)
  expect(screen.queryByRole('button', { name: 'Oui, ces informations sont exactes' })).toBeNull()
  expect((screen.getByRole('textbox', { name: 'Votre message' }) as HTMLTextAreaElement).value).toBe('Une précision à ajouter')
  expect(fetch.mock.calls.filter(([url]) => url.endsWith('/session'))).toHaveLength(1)
})

it('clears an expired restored session and requests the original SMS link', async () => {
  sessionStorage.setItem('claimroom.deposit.session.v1', JSON.stringify({ token: 'expired', caseId: 'case-1',
    phase: 'photos', opening: 'Recap', openingAt: new Date().toISOString(), messages: [], proposal: null, input: '' }))
  window.history.replaceState(null, '', '/depot')
  vi.stubGlobal('fetch', vi.fn(async () => response({ detail: { code: 'expired_session' } }, 401)))
  render(<DepositApp />)
  await screen.findByText('Votre session a expiré. Rouvrez le lien reçu par SMS.')
  expect(sessionStorage.getItem('claimroom.deposit.session.v1')).toBeNull()
  expect(screen.queryByLabelText('Conversation du dossier')).toBeNull()
})

it('uploads multiple files in sequence and retries only failed finalization with the latest version', async () => {
  type Photo = { id: string; kind: string; source_kind: string; mime_type: string; byte_size: number;
    checksum_status: string; original_filename: string; received_at: string }
  let current = { ...initial, intake: { ...initial.intake, incident_at: '2026-09-26T10:00:00+02:00' }, evidence: [] as Photo[] }
  const finalizedVersions: number[] = []
  let failFirst = true
  uploadToSignedUrl.mockReset().mockResolvedValue({ error: null })
  vi.stubGlobal('crypto', { randomUUID: crypto.randomUUID.bind(crypto), subtle: { digest: async () => new Uint8Array(32).buffer } })
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/session')) return response({ session_token: 'guest-session' })
    if (url.endsWith('/corrections')) {
      const body = JSON.parse(String(options?.body))
      current = { ...current, state_version: current.state_version + 1, intake: { ...current.intake, [body.field]: body.value } }
      return response(current)
    }
    if (url.endsWith('/upload-intents')) {
      const body = JSON.parse(String(options?.body))
      return response({ bucket: 'evidence', storage_path: body.filename, token: 'upload', expires_at: '2099-01-01' })
    }
    if (url.endsWith('/evidence')) {
      const body = JSON.parse(String(options?.body))
      finalizedVersions.push(body.expected_state_version)
      if (body.storage_path === 'overview.jpg' && failFirst) { failFirst = false; return response({}, 503) }
      current = { ...current, state_version: current.state_version + 1, content_revision: current.content_revision + 1,
        evidence: [...current.evidence, { id: body.storage_path, kind: 'scene_photo', source_kind: 'insured_upload',
          mime_type: 'image/jpeg', byte_size: 5, checksum_status: 'verified', original_filename: body.storage_path,
          received_at: new Date().toISOString() }] }
      return response(current)
    }
    return response(current)
  })
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  const files = ['overview.jpg', 'damage.jpg'].map(name => {
    const file = new File(['photo'], name, { type: 'image/jpeg' })
    Object.defineProperty(file, 'arrayBuffer', { value: async () => new Uint8Array(5).buffer })
    return file
  })
  fireEvent.change(screen.getByLabelText('Choisir une pièce jointe'), { target: { files } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await screen.findByText(/1 pièce reçue/)
  expect(current.evidence.map(item => item.original_filename)).toEqual(['damage.jpg'])
  expect((screen.getByRole('button', { name: 'Terminer l’ajout' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Réessayer overview.jpg' }))
  await waitFor(() => expect(current.evidence).toHaveLength(2))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Réessayer overview.jpg' })).toBeNull())
  expect(uploadToSignedUrl).toHaveBeenCalledTimes(2)
  expect(finalizedVersions).toEqual([1, 1, 2])
  expect(screen.queryByText(/Votre ajout de pièces est terminé/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Terminer l’ajout' }))
  await screen.findByText(/Votre ajout de pièces est terminé/)
  fireEvent.click(screen.getByText('Modifier directement une information'))
  fireEvent.change(screen.getByLabelText('Champ'), { target: { value: 'location' } })
  fireEvent.change(screen.getByLabelText('Nouvelle valeur'), { target: { value: 'Paris, rue des Lilas' } })
  fireEvent.click(screen.getByRole('button', { name: 'Enregistrer la correction' }))
  await screen.findByText(/Correction enregistrée/ )
  expect(current.intake.location).toBe('Paris, rue des Lilas')
  expect(screen.queryByRole('button', { name: 'Oui, ces informations sont exactes' })).toBeNull()
  expect(screen.getByRole('textbox', { name: 'Votre message' })).toBeTruthy()
})

it('asks for a missing name, confirms it, then stops asking after reopening the SMS link', async () => {
  let current = { ...initial, intake: { ...initial.intake, insured_name: null as string | null,
    incident_at: '2026-09-27T10:00:00Z' }, missing_fields: ['insured_name'] }
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/session')) return response({ session_token: 'new-session' })
    if (url.endsWith('/chat')) return response({ reply: 'À confirmer', proposal: { field: 'insured_name', value: 'Alex Martin' } })
    if (url.endsWith('/corrections')) {
      const body = JSON.parse(String(options?.body))
      expect(body.field).toBe('insured_name')
      current = { ...current, intake: { ...current.intake, insured_name: body.value }, missing_fields: [] }
    }
    return response(current)
  })
  openChat(fetch)
  await screen.findByText(/Bonjour, voici le récapitulatif/)
  fireEvent.click(screen.getByRole('button', { name: 'Oui, ces informations sont exactes' }))
  expect(screen.getByText(/Quels sont vos nom et prénom/)).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: 'Votre message' }), { target: { value: 'Alex Martin' } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await screen.findByText(/J’ai noté Nom et prénom : Alex Martin/)
  expect(JSON.parse(String(fetch.mock.calls.find(([url]) => url.endsWith('/chat'))![1]?.body)).target_field).toBe('insured_name')
  fireEvent.click(screen.getByRole('button', { name: 'Confirmer' }))
  await screen.findByText(/Correction enregistrée/)
  await waitFor(() => expect(screen.queryByText('Sauvegarde de la conversation…')).toBeNull())
  cleanup()
  sessionStorage.clear() // A different tab/device has no browser history.
  openChat(fetch)
  await screen.findByText(/Correction enregistrée/)
  expect(screen.getAllByText(/J’ai noté Nom et prénom : Alex Martin/)).toHaveLength(1)
  expect(screen.queryByRole('button', { name: 'Oui, ces informations sont exactes' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Confirmer' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Terminer l’ajout' })).toBeTruthy()
})

it('restores a pending proposal and both speakers from the server in a new session', async () => {
  storedHistory = { revision: 8, state: {
    phase: 'collect', opening: 'Récapitulatif initial', openingAt: '2026-09-27T10:00:00Z',
    messages: [
      { id: crypto.randomUUID(), role: 'assistant', text: 'Quels sont vos nom et prénom ?', created_at: '2026-09-27T10:01:00Z' },
      { id: crypto.randomUUID(), role: 'insured', text: 'Alex Martin', created_at: '2026-09-27T10:02:00Z' },
      { id: crypto.randomUUID(), role: 'assistant', text: 'Pouvez-vous confirmer votre nom ?', created_at: '2026-09-27T10:03:00Z' },
    ], proposal: { field: 'insured_name', value: 'Alex Martin' },
  } }
  openChat(vi.fn(async (url: string) => response(url.endsWith('/session') ? { session_token: 'new-session' } : initial)))
  await screen.findByText('Récapitulatif initial')
  expect(screen.getByText('Alex Martin')).toBeTruthy()
  expect(screen.getByText('Pouvez-vous confirmer votre nom ?')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Confirmer' })).toBeTruthy()
  expect(screen.queryByText(/Bonjour, voici le récapitulatif/)).toBeNull()
})

it('does not replace an unavailable server history with an empty conversation', async () => {
  window.history.replaceState(null, '', '/depot#token=private-link')
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    expect(options?.method).not.toBe('PUT')
    if (url.endsWith('/session')) return response({ session_token: 'session' })
    if (url.endsWith('/chat-history')) return response({}, 503)
    return response(initial)
  })
  vi.stubGlobal('fetch', fetch)
  render(<DepositApp />)
  await screen.findByRole('alert')
  expect(screen.queryByLabelText('Conversation du dossier')).toBeNull()
})
