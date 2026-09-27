// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SimulatedWhatsAppChat } from './SimulatedWhatsAppChat'

const mocks = vi.hoisted(() => ({
  getCase: vi.fn(), updateIntake: vi.fn(), createEvidenceUploadIntent: vi.fn(),
  finalizeEvidence: vi.fn(), uploadToSignedUrl: vi.fn(),
}))

vi.mock('../lib/api', () => ({
  getCase: mocks.getCase, updateIntake: mocks.updateIntake,
  createEvidenceUploadIntent: mocks.createEvidenceUploadIntent, finalizeEvidence: mocks.finalizeEvidence,
}))
vi.mock('../lib/supabase', () => ({
  supabase: { storage: { from: () => ({ uploadToSignedUrl: mocks.uploadToSignedUrl }) } },
}))

afterEach(() => { cleanup(); sessionStorage.clear(); vi.clearAllMocks(); vi.unstubAllGlobals() })

it('continues the saved voice case through chat answers and a case photo', async () => {
  const first = { id: 'case-1', state_version: 1, intake: { location: null, incident_at: '2026-09-26T10:00:00Z',
    injury_status: 'no', danger_status: 'no', insured_reference: 'S-1', policy_reference: 'P-1',
    insured_vehicle: 'Peugeot 208', insured_plate: 'FR-123-AA', narrative: 'Accident.' }, evidence: [] }
  const located = { ...first, state_version: 2, intake: { ...first.intake, location: 'Paris' } }
  const withPhoto = { ...located, state_version: 3, evidence: [{ id: 'photo-1', kind: 'scene_photo' }] }
  mocks.getCase.mockResolvedValue(first)
  mocks.updateIntake.mockResolvedValue(located)
  mocks.createEvidenceUploadIntent.mockResolvedValue({ bucket: 'private', storage_path: 'case-1/uploads/photo.jpg', token: 'signed' })
  mocks.uploadToSignedUrl.mockResolvedValue({ error: null })
  mocks.finalizeEvidence.mockResolvedValue(withPhoto)
  vi.stubGlobal('crypto', { subtle: { digest: vi.fn().mockResolvedValue(new Uint8Array(32).buffer) } })

  render(<SimulatedWhatsAppChat caseId="case-1" accessToken="manager-jwt" />)
  await screen.findByText(/préciser le lieu/)
  fireEvent.change(screen.getByRole('textbox', { name: 'Votre message' }), { target: { value: 'Paris' } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await waitFor(() => expect(mocks.updateIntake).toHaveBeenCalledWith('manager-jwt', 'case-1', 1, { location: 'Paris' }))
  await screen.findByText(/photo d’ensemble/)

  const photo = new File(['picture'], 'scene.png', { type: 'image/png' })
  Object.defineProperty(photo, 'arrayBuffer', { value: async () => new TextEncoder().encode('picture').buffer })
  fireEvent.change(screen.getByLabelText('Choisir une photo'), { target: { files: [photo] } })
  await waitFor(() => expect(mocks.createEvidenceUploadIntent).toHaveBeenCalledWith('manager-jwt', 'case-1',
    expect.objectContaining({ filename: 'scene.png', kind: 'scene_photo', expected_state_version: 2 })))
  await waitFor(() => expect(mocks.finalizeEvidence).toHaveBeenCalledWith('manager-jwt', 'case-1',
    'case-1/uploads/photo.jpg', expect.any(String), 'scene_photo', 2))
  await screen.findByText(/photo rapprochée/)
  expect(screen.getByText('scene.png')).toBeTruthy()
  await waitFor(() => expect(sessionStorage.getItem('claimroom:conversation:case-1')).toContain('scene.png'))
})

it('asks for missing claim and insured vehicle details before photos, while allowing unavailable values', async () => {
  const first = { id: 'case-2', state_version: 1, intake: { location: 'Paris', incident_at: '2026-09-26T10:00:00Z',
    injury_status: 'no', danger_status: 'no', insured_reference: null, policy_reference: null,
    insured_vehicle: null, insured_plate: null, narrative: 'Accident.' }, evidence: [] }
  const withReference = { ...first, state_version: 2, intake: { ...first.intake, insured_reference: 'SIN-42' } }
  const withVehicle = { ...withReference, state_version: 3, intake: { ...withReference.intake, insured_vehicle: 'Peugeot 208' } }
  mocks.getCase.mockResolvedValue(first)
  mocks.updateIntake.mockResolvedValueOnce(withReference).mockResolvedValueOnce(withVehicle)

  render(<SimulatedWhatsAppChat caseId="case-2" accessToken="manager-jwt" />)
  await screen.findByText(/référence de sinistre/)
  fireEvent.change(screen.getByRole('textbox', { name: 'Votre message' }), { target: { value: 'SIN-42' } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await waitFor(() => expect(mocks.updateIntake).toHaveBeenCalledWith('manager-jwt', 'case-2', 1, { insured_reference: 'SIN-42' }))
  await screen.findByText(/référence de contrat/)
  fireEvent.click(screen.getByRole('button', { name: 'Je ne l’ai pas' }))
  await screen.findByText(/modèle de votre véhicule assuré/)
  fireEvent.change(screen.getByRole('textbox', { name: 'Votre message' }), { target: { value: 'Peugeot 208' } })
  fireEvent.click(screen.getByRole('button', { name: 'Envoyer' }))
  await waitFor(() => expect(mocks.updateIntake).toHaveBeenCalledWith('manager-jwt', 'case-2', 2, { insured_vehicle: 'Peugeot 208' }))
  await screen.findByText(/immatriculation de votre véhicule assuré/)
  fireEvent.click(screen.getByRole('button', { name: 'Je ne l’ai pas' }))
  await screen.findByText(/photo d’ensemble/)
})
