import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { mapCameras } from '../lib/api'
import type { CameraMap as CameraMapData } from '../lib/api'

type Props = { caseId: string; address: string | null; accessToken: string; radiusM?: number }

function MapCanvas({ result }: { result: CameraMapData }) {
  const container = useRef<HTMLDivElement>(null)
  const markers = useRef<Map<string, L.CircleMarker>>(new Map())
  const map = useRef<L.Map | null>(null)

  useEffect(() => {
    if (result.mode === 'mock' || !container.current || result.latitude === null || result.longitude === null) return
    const center: L.LatLngExpression = [result.latitude, result.longitude]
    const leaflet = L.map(container.current, { scrollWheelZoom: false }).setView(center, 15)
    map.current = leaflet
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
      maxZoom: 19,
      // OSM requires a Referer; send only the site origin, never the page URL or deposit token.
      referrerPolicy: 'origin',
    }).addTo(leaflet)
    const radius = L.circle(center, { radius: result.radius_m, color: '#4d8f79',
      weight: 1.5, fillColor: '#9bcbb5', fillOpacity: .12 }).addTo(leaflet)
    L.circleMarker(center, { radius: 8, color: '#fff', weight: 2, fillColor: '#176d57',
      fillOpacity: 1 }).addTo(leaflet).bindPopup('Adresse du sinistre')
    for (const camera of result.cameras) {
      const popup = document.createElement('span')
      popup.textContent = `${camera.label} · ${camera.distance_m} m de l’adresse`
      const marker = L.circleMarker([camera.latitude, camera.longitude], {
        radius: 7, color: '#fff', weight: 2, fillColor: '#c47b38', fillOpacity: 1,
      }).addTo(leaflet).bindPopup(popup)
      markers.current.set(camera.id, marker)
    }
    const bounds = radius.getBounds()
    for (const camera of result.cameras) bounds.extend([camera.latitude, camera.longitude])
    let frame = 0
    let fitted = false
    const resize = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        if (!container.current?.clientWidth || !container.current.clientHeight) return
        leaflet.invalidateSize({ pan: false })
        if (!fitted) {
          leaflet.fitBounds(bounds, { padding: [12, 12] })
          fitted = true
        }
      })
    }
    const observer = new ResizeObserver(resize)
    observer.observe(container.current)
    resize()
    return () => {
      observer.disconnect(); cancelAnimationFrame(frame)
      markers.current.clear(); map.current = null; leaflet.remove()
    }
  }, [result])

  function showCamera(id: string) {
    const marker = markers.current.get(id)
    if (marker && map.current) {
      map.current.panTo(marker.getLatLng())
      marker.openPopup()
    }
  }

  return <>
    {result.mode === 'mock' ? <div className="ir-camera-map ir-camera-map-demo" role="img"
      aria-label={`Plan illustratif du lieu fictif avec une caméra de démonstration à ${result.cameras[0]?.distance_m || 0} mètres`}>
      <span className="ir-demo-map-label">Plan illustratif · démonstration</span>
      <span className="ir-demo-road ir-demo-road-horizontal" /><span className="ir-demo-road ir-demo-road-vertical" />
      <span className="ir-demo-place-dot">Lieu déclaré</span><span className="ir-demo-camera-dot">Caméra</span>
    </div> : <div className="ir-camera-map" ref={container} role="img"
      aria-label={`Carte OpenStreetMap autour de ${result.resolved_address || result.address}, ${result.cameras.length} caméras Camérci affichées, rayon de recherche ${result.radius_m} mètres`} />}
    <div className="ir-camera-map-legend"><span className="ir-map-address-dot" /> Lieu <span className="ir-map-camera-dot" /> Caméra {result.mode === 'mock' ? 'fictive' : 'Camérci'} <span>· Rayon {result.radius_m} m</span></div>
    {result.cameras.length > 0 && <ol className="ir-camera-map-list">
      {result.cameras.map(camera => <li key={camera.id}>
        {result.mode === 'mock' ? <strong>{camera.label}</strong> : <button type="button" onClick={() => showCamera(camera.id)}>{camera.label}</button>}
        <span>{camera.distance_m} m{camera.distance_m > result.radius_m ? ' · Hors du rayon' : ''}{camera.operator ? ` · ${camera.operator}` : ''}</span>
        {camera.source_url && <a href={camera.source_url} target="_blank" rel="noopener noreferrer">Voir sur Camérci ↗</a>}
      </li>)}
    </ol>}
  </>
}

export function CameraMap({ caseId, address, accessToken, radiusM = 150 }: Props) {
  const [result, setResult] = useState<CameraMapData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let active = true
    setResult(null)
    setError('')
    setLoading(true)
    void mapCameras(accessToken, caseId, radiusM)
      .then(value => { if (active) setResult(value) })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Carte indisponible.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [accessToken, caseId, address, radiusM])

  const inRadiusCount = result?.cameras.filter(camera => camera.distance_m <= result.radius_m).length || 0

  return <div className="ir-camera-map-section">
    <h3>Caméras autour du lieu · {radiusM} m</h3>
    <p>Adresse déclarée : <strong>{address || 'à préciser'}</strong></p>
    {loading && <p role="status">Recherche des caméras proches…</p>}
    {!loading && error && <p className="ir-error" role="alert">{error}</p>}
    {!loading && result?.reason && <p role="status">{result.reason}</p>}
    {!loading && result?.resolved_address && <p>Position géocodée indicative : <strong>{result.resolved_address}</strong></p>}
    {!loading && result?.status === 'available' && result.mode !== 'mock' && inRadiusCount > 0 && <p><strong>{inRadiusCount} caméra{inRadiusCount > 1 ? 's' : ''}</strong> répertoriée{inRadiusCount > 1 ? 's' : ''} par Camérci dans un rayon de {result.radius_m} m.</p>}
    {!loading && result?.cameras[0] && <p className="ir-nearest-camera"><strong>La plus proche : {result.cameras[0].label}</strong> · {result.cameras[0].distance_m} m{result.cameras[0].operator ? ` · ${result.cameras[0].operator}` : ''}</p>}
    {!loading && result && (result.mode === 'mock' || result.latitude !== null && result.longitude !== null) && <MapCanvas result={result} />}
    <p className="ir-camera-map-note">{result?.mode === 'mock' ? 'Caméra et plan fictifs pour l’aperçu. Aucun enregistrement réel n’a été recherché.' : <>Caméras : <a href="https://camerci.fr/" target="_blank" rel="noopener noreferrer">Camérci</a> (CC BY 4.0) · Fond de carte : OpenStreetMap. Le catalogue couvre seulement certains secteurs et peut être ancien. Un marqueur ne garantit ni couverture du lieu, ni enregistrement disponible.</>}</p>
  </div>
}
