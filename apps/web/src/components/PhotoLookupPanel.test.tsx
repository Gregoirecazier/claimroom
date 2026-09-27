// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { PhotoLookupPanel } from './PhotoLookupPanel'
import { accidentVideo, getPhotoVideoMatch } from '../lib/api'
import type { Evidence } from '../lib/api'

vi.mock('./CameraMap', () => ({ CameraMap: ({ radiusM = 150 }: { radiusM?: number }) => <div>Rayon {radiusM} m</div> }))
vi.mock('../lib/api', () => ({ getPhotoVideoMatch: vi.fn(), accidentVideo: vi.fn() }))

const photos = [
  { id: 'photo-1', kind: 'scene_photo', mime_type: 'image/png' },
  { id: 'photo-2', kind: 'damage_photo', mime_type: 'image/png' },
] as Evidence[]
const videos = [{ id: 'video-1', role: 'video_g1', mime_type: 'video/mp4' }] as Evidence[]

afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks() })

it('automatically links both photos to their existing video in the second block', async () => {
  vi.mocked(getPhotoVideoMatch).mockImplementation(async (_token, _caseId, photoId) =>
    ({ status: 'matched', photo_id: photoId, plate: 'FR482KL', video_id: 'video-1' }))
  const open = vi.fn()
  render(<PhotoLookupPanel caseId="case-1" address="Rue test" accessToken="token"
    photos={photos} videos={videos} onOpenEvidence={open} />)
  expect(screen.getByText('Rayon 150 m')).toBeTruthy()
  await waitFor(() => expect(getPhotoVideoMatch).toHaveBeenCalledTimes(2))
  expect(await screen.findByText('Plaque FR482KL · photos 1, 2 · démonstration')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Chercher la vidéo correspondante' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Voir' }))
  expect(open).toHaveBeenCalledWith(videos[0])
})

it('automatically offers a catalogue video when it is not attached as evidence', async () => {
  vi.mocked(getPhotoVideoMatch).mockImplementation(async (_token, _caseId, photoId) =>
    ({ status: photoId === 'photo-1' ? 'matched' : 'no_match', photo_id: photoId,
      plate: photoId === 'photo-1' ? 'FR482KL' : null, video_id: photoId === 'photo-1' ? 'archive-1' : null }))
  vi.mocked(accidentVideo).mockResolvedValue('blob:matched-video')
  render(<PhotoLookupPanel caseId="case-1" address="Rue test" accessToken="token"
    photos={photos} videos={[]} onOpenEvidence={vi.fn()} />)
  expect(await screen.findByText('Plaque FR482KL · photo 1 · catalogue synthétique')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Voir' }))
  await waitFor(() => expect(accidentVideo).toHaveBeenCalledWith('token', 'case-1', 'archive-1'))
  expect((screen.getByLabelText('Vidéo correspondante archive-1') as HTMLVideoElement).src).toBe('blob:matched-video')
})

it('does not offer a video without a source-backed match', async () => {
  vi.mocked(getPhotoVideoMatch).mockImplementation(async (_token, _caseId, photoId) =>
    ({ status: 'no_match', photo_id: photoId, plate: null, video_id: null }))
  render(<PhotoLookupPanel caseId="case-1" address="Rue test" accessToken="token"
    photos={photos} videos={[]} onOpenEvidence={vi.fn()} />)
  expect(await screen.findByText('Aucune vidéo associée pour le moment.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Voir' })).toBeNull()
})

it('refreshes pending matches when analysis finishes without a photo or revision change', async () => {
  vi.useFakeTimers()
  let ready = false
  vi.mocked(getPhotoVideoMatch).mockImplementation(async (_token, _caseId, photoId) =>
    ({ status: ready ? 'matched' : 'pending', photo_id: photoId,
      plate: ready ? 'FR482KL' : null, video_id: ready ? 'archive-1' : null }))
  render(<PhotoLookupPanel caseId="case-1" address="Rue test" accessToken="token"
    photos={photos} videos={[]} onOpenEvidence={vi.fn()} />)
  await act(async () => {})
  expect(screen.getByText('Correspondance automatique en cours…')).toBeTruthy()
  expect(screen.queryByText('Aucune vidéo associée pour le moment.')).toBeNull()
  ready = true
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(screen.getByText('Plaque FR482KL · photos 1, 2 · catalogue synthétique')).toBeTruthy()
  expect(screen.queryByText('Correspondance automatique en cours…')).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(getPhotoVideoMatch).toHaveBeenCalledTimes(4)
})

it('retries a partial lookup failure instead of treating it as no match', async () => {
  vi.useFakeTimers()
  let recovered = false
  vi.mocked(getPhotoVideoMatch).mockImplementation(async (_token, _caseId, photoId) => {
    if (photoId === 'photo-2' && !recovered) throw new Error('Unavailable')
    return { status: 'no_match', photo_id: photoId, plate: null, video_id: null }
  })
  render(<PhotoLookupPanel caseId="case-1" address="Rue test" accessToken="token"
    photos={photos} videos={[]} onOpenEvidence={vi.fn()} />)
  await act(async () => {})
  expect(screen.getByRole('alert').textContent).toContain('Nouvelle tentative automatique')
  expect(screen.queryByText('Aucune vidéo associée pour le moment.')).toBeNull()
  recovered = true
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText('Aucune vidéo associée pour le moment.')).toBeTruthy()
})

it('cancels pending checks when leaving the dossier', async () => {
  vi.useFakeTimers()
  vi.mocked(getPhotoVideoMatch).mockImplementation(async (_token, _caseId, photoId) =>
    ({ status: 'pending', photo_id: photoId, plate: null, video_id: null }))
  const { unmount } = render(<PhotoLookupPanel caseId="case-1" address="Rue test" accessToken="token"
    photos={photos} videos={[]} onOpenEvidence={vi.fn()} />)
  await act(async () => {})
  unmount()
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(getPhotoVideoMatch).toHaveBeenCalledTimes(2)
})
