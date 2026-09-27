import { useEffect, useState } from 'react'
import { accidentVideo, getPhotoVideoMatch } from '../lib/api'
import type { Evidence, PhotoVideoMatch } from '../lib/api'
import { CameraMap } from './CameraMap'

export function PhotoLookupPanel({ caseId, address, accessToken, photos, videos, onOpenEvidence }: {
  caseId: string; address: string | null; accessToken: string; photos: Evidence[]; videos: Evidence[]
  onOpenEvidence: (evidence: Evidence) => void
}) {
  const [matches, setMatches] = useState<PhotoVideoMatch[]>([])
  const [checking, setChecking] = useState(false)
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [videoId, setVideoId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const photoIds = photos.map(photo => photo.id).join(',')

  useEffect(() => () => { if (videoUrl) URL.revokeObjectURL(videoUrl) }, [videoUrl])
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    setMatches([]); setVideoUrl(null); setVideoId(null); setError('')
    if (!photoIds) { setChecking(false); return () => { active = false } }
    setChecking(true)
    async function checkMatches() {
      const results = await Promise.allSettled(photoIds.split(',').map(id => getPhotoVideoMatch(accessToken, caseId, id)))
      if (!active) return
      const next = results.filter((result): result is PromiseFulfilledResult<PhotoVideoMatch> => result.status === 'fulfilled')
        .map(result => result.value)
      const failed = results.some(result => result.status === 'rejected')
      const pending = next.some(match => match.status === 'pending')
      setMatches(next)
      setError(failed ? 'Correspondance des vidéos indisponible pour certaines photos. Nouvelle tentative automatique…' : '')
      setChecking(pending)
      // The analysis can finish without changing the photos or case revision.
      // Keep waiting for its result instead of displaying a definitive absence.
      if (pending || failed) timer = setTimeout(() => void checkMatches(), 5000)
    }
    void checkMatches()
    return () => { active = false; clearTimeout(timer) }
  }, [accessToken, caseId, photoIds])

  const linked = new Map<string, { plate: string; photoNumbers: number[] }>()
  for (const match of matches) {
    if (match.status !== 'matched' || !match.video_id || !match.plate) continue
    const number = photos.findIndex(photo => photo.id === match.photo_id) + 1
    if (!number) continue
    const found = linked.get(match.video_id)
    if (found) { if (!found.photoNumbers.includes(number)) found.photoNumbers.push(number) }
    else linked.set(match.video_id, { plate: match.plate, photoNumbers: [number] })
  }
  const archiveMatches = [...linked].filter(([id]) => !videos.some(video => video.id === id))

  async function showVideo(id: string) {
    setBusy(true); setError('')
    try { setVideoUrl(await accidentVideo(accessToken, caseId, id)); setVideoId(id) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Vidéo indisponible.') }
    finally { setBusy(false) }
  }

  return <section className="ir-card ir-photo-lookup" aria-labelledby="photo-lookup-title">
    <div className="ir-section-heading"><span className="ir-heading-icon">⌖</span><div><h2 id="photo-lookup-title">Recherche caméra et vidéo</h2></div></div>
    <CameraMap caseId={caseId} address={address} accessToken={accessToken} />
    <div className="ir-photo-match">
      <h3>Vidéos associées aux photos</h3>
      {checking && <p role="status">Correspondance automatique en cours…</p>}
      {!checking && !photos.length && <p>Ajoutez une photo pour lancer la recherche.</p>}
      {!checking && photos.length > 0 && !videos.length && !linked.size && !error &&
        <p role="status">Aucune vidéo associée pour le moment.</p>}
      {videos.map(video => {
        const match = linked.get(video.id)
        return <div className="ir-video-row" key={video.id}>
          <span className="ir-video-icon">▶</span><div>
            <strong>{video.role?.startsWith('video_g') ? `Vidéo ${video.role.replace('video_', '').toUpperCase()} · MP4` : 'Vidéo ajoutée'}</strong>
            <p>{match ? `Plaque ${match.plate} · photo${match.photoNumbers.length > 1 ? 's' : ''} ${match.photoNumbers.join(', ')} · démonstration` : 'Pièce vidéo du dossier'}</p>
          </div><button className="ir-button ir-secondary" onClick={() => onOpenEvidence(video)}>Voir</button>
        </div>
      })}
      {archiveMatches.map(([id, match]) => <div className="ir-video-row" key={id}>
        <span className="ir-video-icon">▶</span><div><strong>Vidéo correspondante</strong>
          <p>Plaque {match.plate} · photo{match.photoNumbers.length > 1 ? 's' : ''} {match.photoNumbers.join(', ')} · catalogue synthétique</p>
        </div><button className="ir-button ir-secondary" disabled={busy} onClick={() => void showVideo(id)}>Voir</button>
      </div>)}
      {videoUrl && <video className="ir-photo-match-video" controls src={videoUrl} aria-label={`Vidéo correspondante ${videoId || ''}`} />}
      {error && <p className="ir-error" role="alert">{error}</p>}
    </div>
  </section>
}
