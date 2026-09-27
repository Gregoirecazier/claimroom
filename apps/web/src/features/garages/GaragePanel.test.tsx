// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import GaragePanel from './GaragePanel'
import L from 'leaflet'
import { previewGarages } from '../../lib/api'
import type { GaragePreview } from '../../lib/api'
import { reviewableCase } from '../../lib/caseJourney.fixture'

vi.mock('../../lib/api', () => ({ previewGarages: vi.fn() }))
vi.mock('leaflet', () => ({ default: {
  map: () => ({ setView() { return this }, fitBounds: vi.fn(), invalidateSize: vi.fn(), remove: vi.fn() }),
  tileLayer: () => ({ addTo: vi.fn() }),
  latLngBounds: () => ({ extend: vi.fn(), pad: vi.fn() }),
  divIcon: vi.fn(),
  marker: vi.fn(() => ({ addTo() { return this }, on() { return this }, bindTooltip: vi.fn(), setIcon: vi.fn() })),
} }))
const result = (): GaragePreview => ({ status: 'ready', origin: { latitude: 48.85, longitude: 2.35 }, radius_m: 500,
  garages: [{ osm_id: 'node/1', name: 'Garage de quartier', address: '1 rue du Test', latitude: 48.85, longitude: 2.35, distance_m: 200, directions_url: 'https://www.google.com/maps/dir/?api=1&destination=48.85,2.35' }],
  location_candidates: [], sms_body: null, attribution: '© OpenStreetMap contributors', attribution_url: 'https://www.openstreetmap.org/copyright',
})
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.mocked(previewGarages).mockResolvedValue(result())
})
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals() })
it('finds nearby garages without a partner network and does not repeat lookup on case polling', async () => {
  const caseView = { ...reviewableCase(), partner_garages: [] }
  const { rerender } = render(<GaragePanel caseView={caseView} accessToken="test" />)
  expect(await screen.findByText('Garage de quartier')).toBeTruthy()
  expect(previewGarages).toHaveBeenCalledExactlyOnceWith('test', caseView.id, caseView.state_version, null)
  expect(screen.getByRole('button', { name: /Garage de quartier/ }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.queryByRole('link', { name: /Itinéraire/ })).toBeNull()
  rerender(<GaragePanel caseView={{ ...caseView, state_version: caseView.state_version + 1 }} accessToken="test" />)
  expect(previewGarages).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Relancer la recherche' }))
  await waitFor(() => expect(previewGarages).toHaveBeenLastCalledWith('test', caseView.id, caseView.state_version + 1, result().origin))
})
it('lets the user resolve an ambiguous address before searching', async () => {
  vi.mocked(previewGarages).mockResolvedValueOnce({ ...result(), status: 'needs_location', origin: null, garages: [], location_candidates: [{ latitude: 48.85, longitude: 2.35, label: 'Adresse proposée', precise: true }] })
  render(<GaragePanel caseView={reviewableCase()} accessToken="test" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Adresse proposée' }))
  expect(await screen.findByText('Garage de quartier')).toBeTruthy()
  expect(previewGarages).toHaveBeenLastCalledWith('test', reviewableCase().id, reviewableCase().state_version, { latitude: 48.85, longitude: 2.35 })
})
it('offers a retry after a failed lookup and explains empty results', async () => {
  vi.mocked(previewGarages).mockRejectedValueOnce(new Error('osm_unavailable')).mockResolvedValueOnce({ ...result(), status: 'no_results', garages: [], radius_m: 5000 })
  render(<GaragePanel caseView={reviewableCase()} accessToken="test" />)
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Voir les garages sur Google Maps' }).getAttribute('href')).toContain('https://www.google.com/maps/search/')
  fireEvent.click(screen.getByRole('button', { name: 'Relancer la recherche' }))
  expect(await screen.findByText('Aucun garage trouvé à moins de 5 km de cette adresse.')).toBeTruthy()
})

it('shows possible addresses on the map without inventing an accident location', async () => {
  vi.mocked(previewGarages).mockResolvedValueOnce({ ...result(), status: 'needs_location', origin: null, garages: [], location_candidates: [
    { latitude: 48.85, longitude: 2.35, label: 'Paris', precise: true },
    { latitude: 45.83, longitude: 1.26, label: 'Limoges', precise: true },
  ] })
  render(<GaragePanel caseView={reviewableCase()} accessToken="test" />)
  expect(await screen.findByRole('img', { name: 'Carte des adresses possibles du lieu de l’accident' })).toBeTruthy()
  await waitFor(() => expect(L.marker).toHaveBeenCalledWith([45.83, 1.26], expect.objectContaining({ title: 'Adresse possible 2 : Limoges' })))
  expect(L.marker).not.toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ title: 'Lieu de l’accident' }))
})
it('keeps the accident marker visible while loading and after a nearby lookup fails', async () => {
  let fail!: (error: Error) => void
  vi.mocked(previewGarages).mockReset().mockResolvedValueOnce({ ...result(), status: 'needs_location', origin: null, garages: [], location_candidates: [{ latitude: 48.85, longitude: 2.35, label: 'Paris', precise: true }] }).mockImplementationOnce(() => new Promise((_, reject) => { fail = reject }))
  render(<GaragePanel caseView={reviewableCase()} accessToken="test" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Paris' }))
  expect(screen.getByRole('img', { name: 'Carte du lieu de l’accident et des 0 garages à proximité' })).toBeTruthy()
  await waitFor(() => expect(L.marker).toHaveBeenCalledWith([48.85, 2.35], expect.objectContaining({ title: 'Lieu de l’accident' })))
  fail(new Error('osm_unavailable'))
  await screen.findByRole('alert')
  expect(screen.getByRole('img', { name: 'Carte du lieu de l’accident et des 0 garages à proximité' })).toBeTruthy()
})
it('shows the accident on the map even when there are no garages', async () => {
  vi.mocked(previewGarages).mockResolvedValueOnce({ ...result(), status: 'no_results', garages: [], radius_m: 5000 })
  render(<GaragePanel caseView={reviewableCase()} accessToken="test" />)
  expect(await screen.findByRole('img', { name: 'Carte du lieu de l’accident et des 0 garages à proximité' })).toBeTruthy()
  await waitFor(() => expect(L.marker).toHaveBeenCalledWith([48.85, 2.35], expect.objectContaining({ title: 'Lieu de l’accident' })))
})

it('automatically chooses Paris in ambiguous demo examples and places the accident there', async () => {
  vi.mocked(previewGarages).mockResolvedValueOnce({ ...result(), status: 'needs_location', origin: null, garages: [], location_candidates: [
    { latitude: 45.83, longitude: 1.26, label: '30, Rue de Babylone, Limoges, France', precise: true },
    { latitude: 48.85, longitude: 2.35, label: '30, Rue de Babylone, Paris, Île-de-France, 75007, France', precise: true },
  ] })
  render(<GaragePanel caseView={reviewableCase()} accessToken="test" />)
  expect(await screen.findByText('Garage de quartier')).toBeTruthy()
  expect(previewGarages).toHaveBeenLastCalledWith('test', reviewableCase().id, reviewableCase().state_version, { latitude: 48.85, longitude: 2.35 })
  expect(screen.queryByRole('button', { name: /Limoges/ })).toBeNull()
  expect(screen.getByRole('img', { name: 'Carte du lieu de l’accident et des 1 garages à proximité' })).toBeTruthy()
})
