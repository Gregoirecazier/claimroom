// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CameraMap } from './CameraMap'
import { mapCameras } from '../lib/api'
import type { CameraMap as CameraMapData } from '../lib/api'

const leaflet = vi.hoisted(() => ({ fitBounds: vi.fn(), extend: vi.fn() }))
vi.mock('../lib/api', () => ({ mapCameras: vi.fn() }))
vi.mock('leaflet', () => ({ default: {
  map: () => {
    const map = { setView: () => map, invalidateSize: vi.fn(), fitBounds: leaflet.fitBounds, remove: vi.fn() }
    return map
  },
  tileLayer: () => ({ addTo: vi.fn() }),
  circle: () => ({ addTo: () => ({ getBounds: () => ({ extend: leaflet.extend }) }) }),
  circleMarker: () => {
    const marker = { addTo: () => marker, bindPopup: () => marker }
    return marker
  },
} }))

const result: CameraMapData = {
  status: 'available', address: '30 rue de Babylone',
  resolved_address: '30 rue de Babylone, Paris', latitude: 48.8517285, longitude: 2.3210618,
  radius_m: 150, reason: null,
  cameras: [{ id: 'paris/578', latitude: 48.85165085, longitude: 2.32184486,
    distance_m: 58, label: '23 rue de Babylone', operator: 'Paris', camera_type: null,
    source_url: 'https://camerci.fr/#17/48.851651/2.321845' }],
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(400)
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); vi.unstubAllGlobals() })

it('requests 150 m and displays the camera at 58 m', async () => {
  vi.mocked(mapCameras).mockResolvedValue(result)
  render(<CameraMap caseId="case-1" address={result.address} accessToken="token" />)
  expect(await screen.findByRole('button', { name: '23 rue de Babylone' })).toBeTruthy()
  expect(mapCameras).toHaveBeenCalledWith('token', 'case-1', 150)
  expect(screen.getByRole('heading', { name: 'Caméras autour du lieu · 150 m' })).toBeTruthy()
  expect(screen.getByText(/répertoriée par Camérci dans un rayon de 150 m/)).toBeTruthy()
  expect(screen.queryByText(/Hors du rayon/)).toBeNull()
})

it('includes the nearest fallback in the map bounds without claiming it is inside 150 m', async () => {
  vi.mocked(mapCameras).mockResolvedValue({ ...result,
    reason: 'La caméra la plus proche du catalogue est affichée hors du rayon de recherche.',
    cameras: [{ ...result.cameras[0], distance_m: 290, latitude: 48.8502, longitude: 2.32428 }],
  })
  render(<CameraMap caseId="case-1" address={result.address} accessToken="token" />)
  expect(await screen.findByText('290 m · Hors du rayon · Paris')).toBeTruthy()
  expect(screen.queryByText(/répertoriée par Camérci dans un rayon/)).toBeNull()
  expect(leaflet.extend).toHaveBeenCalledWith([48.8502, 2.32428])
  await waitFor(() => expect(leaflet.fitBounds).toHaveBeenCalled())
})

it('preserves unavailable data instead of displaying a fabricated camera', async () => {
  vi.mocked(mapCameras).mockResolvedValue({ ...result, status: 'unavailable',
    latitude: null, longitude: null, cameras: [], reason: 'Catalogue indisponible.',
  })
  render(<CameraMap caseId="case-1" address={result.address} accessToken="token" />)
  expect(await screen.findByText('Catalogue indisponible.')).toBeTruthy()
  expect(screen.queryByRole('img')).toBeNull()
  expect(screen.queryByRole('button')).toBeNull()
})
