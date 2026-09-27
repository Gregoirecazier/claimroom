// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { CaseView } from '../lib/api'
import { VoiceIntakePanel } from './VoiceIntakePanel'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const caseView = {
  id: '00000000-0000-4000-8000-000000000001',
  voice_session: {
    provider: 'vapi', session_id: 'call-test', mode: 'live', telephony_provider: 'twilio', status: 'incomplete',
    missing_p0: ['location', 'third_party_involved', 'danger_status', 'injury_severity'], reason_codes: [],
    facts: [
      { field: 'narrative', value: 'Je viens d’avoir un accident', excerpt: 'Je viens d’avoir un accident', uncertainty: 'explicit' },
      { field: 'insured_name', value: 'Marie Martin', excerpt: 'Je suis Marie Martin', uncertainty: 'explicit' },
    ], call_started_at: '2026-09-25T08:00:00Z',
    segments: [
      { id: 'a', speaker: 'assistant', text: 'Bonjour', start_ms: 0, end_ms: 1000 },
      { id: 'b', speaker: 'caller', text: 'Je viens d’avoir un accident', start_ms: 1500, end_ms: 3200 },
    ],
    recording: { status: 'available', mime_type: 'audio/wav', byte_size: 48, sha256: 'a'.repeat(64), error_code: null },
  },
} as unknown as CaseView

it('shows ordered final turns and fetches private audio only on manager request', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ url: 'https://storage.example.test/signed', expires_in: 300, mime_type: 'audio/wav' }) })
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
  vi.stubGlobal('fetch', fetch)
  render(<VoiceIntakePanel caseView={caseView} accessToken="manager-token" />)
  expect(screen.getByRole('heading', { name: 'Échange téléphonique' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Informations recueillies' })).toBeTruthy()
  const transcript = screen.getByRole('list', { name: 'Transcription de l’appel' })
  expect(within(transcript).getByText('Bonjour').compareDocumentPosition(within(transcript).getByText('Je viens d’avoir un accident')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.getByRole('list', { name: 'Transcription de l’appel' }).children[0].className).toContain('ir-voice-turn-agent')
  expect(screen.getByRole('list', { name: 'Transcription de l’appel' }).children[1].className).toContain('ir-voice-turn-caller')
  expect(screen.getByText('00:01–00:03')).toBeTruthy()
  expect(screen.getByText('2 / 3 renseignées')).toBeTruthy()
  expect(within(screen.getByRole('row', { name: /Lieu de l’accident/ })).getByText('À confirmer')).toBeTruthy()
  expect(within(screen.getByRole('row', { name: /Nom et prénom/ })).getByText('Marie Martin')).toBeTruthy()
  expect(screen.queryByRole('row', { name: /Date et heure/ })).toBeNull()
  expect(screen.queryByRole('row', { name: /Autre véhicule impliqué|Présence du tiers|Danger actuel|Blessures/ })).toBeNull()
  expect(fetch).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Écouter l’appel' }))
  await waitFor(() => expect(screen.getByLabelText('Bande son originale de l’appel').getAttribute('src')).toBe('https://storage.example.test/signed'))
  expect(play).toHaveBeenCalledOnce()
  expect(fetch.mock.calls[0][1].headers.Authorization).toBe('Bearer manager-token')
})

it('keeps a first name alone to confirm', () => {
  const voice = caseView.voice_session!
  const facts = voice.facts.map(fact => fact.field === 'insured_name' ? { ...fact, value: 'Marie', excerpt: 'Je suis Marie' } : fact)
  render(<VoiceIntakePanel caseView={{ ...caseView, voice_session: { ...voice, facts, missing_p0: ['location'] } }} accessToken="manager-token" />)
  expect(screen.getByText('1 / 3 renseignées')).toBeTruthy()
  expect(within(screen.getByRole('row', { name: /Nom et prénom/ })).getByText('À confirmer')).toBeTruthy()
})

it('does not require third-party details when another vehicle is involved', () => {
  const voice = caseView.voice_session!
  const involved = { ...voice, facts: [...voice.facts, { field: 'third_party_involved', value: 'yes', excerpt: 'Un autre véhicule', uncertainty: 'explicit' }], missing_p0: [...voice.missing_p0.filter(field => field !== 'third_party_involved'), 'third_party_presence'] }
  render(<VoiceIntakePanel caseView={{ ...caseView, voice_session: involved }} accessToken="manager-token" />)
  expect(screen.getByText('2 / 3 renseignées')).toBeTruthy()
  expect(screen.queryByRole('row', { name: /Présence du tiers/ })).toBeNull()
})

it('shows an ambiguous place without counting it as confirmed', () => {
  const voice = caseView.voice_session!
  const location = { field: 'location', value: "places de l'étoile", excerpt: "Il y a eu deux places de l'étoile.", uncertainty: 'uncertain' }
  render(<VoiceIntakePanel caseView={{ ...caseView, voice_session: { ...voice, facts: [...voice.facts, location] } }} accessToken="manager-token" />)
  expect(screen.getByText('2 / 3 renseignées')).toBeTruthy()
  const row = screen.getByRole('row', { name: /Lieu de l’accident/ })
  expect(within(row).getByText("places de l'étoile")).toBeTruthy()
  expect(within(row).getByText('À confirmer')).toBeTruthy()
})

it('offers re-extraction of an incomplete saved call and refreshes the case', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ replayed: false }) })
  const onUpdated = vi.fn()
  vi.stubGlobal('fetch', fetch)
  render(<VoiceIntakePanel caseView={caseView} accessToken="manager-token" onUpdated={onUpdated} />)
  fireEvent.click(screen.getByRole('button', { name: 'Actualiser les informations recueillies' }))
  await waitFor(() => expect(onUpdated).toHaveBeenCalledOnce())
  expect(fetch.mock.calls[0][0]).toContain('/v1/voice/00000000-0000-4000-8000-000000000001/reextract')
  expect(fetch.mock.calls[0][1].method).toBe('POST')
})

it('shows the structured address first and the caller quote below it', () => {
  const voice = caseView.voice_session!
  const location = { field: 'location', value: '10 Boulevard de Port-Royal', excerpt: 'j’étais 10 boulevard de Port-Royal', uncertainty: 'explicit' }
  render(<VoiceIntakePanel caseView={{ ...caseView, voice_session: { ...voice, facts: [...voice.facts, location], missing_p0: voice.missing_p0.filter(field => field !== 'location') } }} accessToken="manager-token" />)

  const locationRow = screen.getByRole('row', { name: /Lieu de l’accident/ })
  const value = within(locationRow).getByText('10 Boulevard de Port-Royal')
  const quote = within(locationRow).getByText('« j’étais 10 boulevard de Port-Royal »')
  expect(value.compareDocumentPosition(quote) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(value.className).toContain('ir-voice-value')
  expect(quote.className).toContain('ir-voice-excerpt')
  expect(within(screen.getByRole('row', { name: /Ce qui s’est passé/ })).getByText('« Je viens d’avoir un accident »')).toBeTruthy()
})

it('makes an unavailable recording visible', () => {
  render(<VoiceIntakePanel caseView={{ ...caseView, voice_session: { ...caseView.voice_session!, recording: { ...caseView.voice_session!.recording, status: 'error', error_code: 'recording_download_failed' } } }} accessToken="manager-token" />)
  expect((screen.getByRole('button', { name: /Échec de récupération de l’enregistrement/ }) as HTMLButtonElement).disabled).toBe(true)
})

it('summarizes the accident in a short bold title and keeps the complete caller quote below', () => {
  const narrative = "Oui, bonjour. Je suis au 12 rue de l'Appel. En fait, à 17h aujourd'hui, j'ai eu un accident important avec ma Renault Mégane. Et elle s'est faite cartonner et c'est un peu le bordel. Donc, je voudrais savoir ce que je peux faire."
  const voice = caseView.voice_session!
  const facts = voice.facts.map(fact => fact.field === 'narrative' ? { ...fact, value: narrative, excerpt: narrative } : fact)
  render(<VoiceIntakePanel caseView={{ ...caseView, voice_session: { ...voice, facts } }} accessToken="manager-token" />)

  const row = screen.getByRole('row', { name: /Ce qui s’est passé/ })
  const title = within(row).getByText('Accident important avec ma Renault Mégane')
  const detail = within(row).getByText(`« ${narrative} »`)
  expect(title.tagName).toBe('STRONG')
  expect(title.compareDocumentPosition(detail) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(within(row).queryByText(narrative)).toBeNull()
})

it.each([
  ['Bonjour. Je n’ai pas heurté la voiture, elle était déjà abîmée.', 'Je n’ai pas heurté la voiture, elle était déjà abîmée'],
  ['Bonjour. Si j’ai eu un accident, je ne m’en suis pas aperçu.', 'Si j’ai eu un accident, je ne m’en suis pas aperçu'],
  ['Bonjour. Je voudrais des renseignements.', 'Récit de l’accident'],
])('preserves the meaning of the declaration: %s', (narrative, title) => {
  const voice = caseView.voice_session!
  const facts = voice.facts.map(fact => fact.field === 'narrative' ? { ...fact, value: narrative, excerpt: narrative } : fact)
  render(<VoiceIntakePanel caseView={{ ...caseView, voice_session: { ...voice, facts } }} accessToken="manager-token" />)
  const row = screen.getByRole('row', { name: /Ce qui s’est passé/ })
  expect(within(row).getByText(title).tagName).toBe('STRONG')
  expect(within(row).getByText(`« ${narrative} »`)).toBeTruthy()
})
