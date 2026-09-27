import { useCallback, useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { previewGarages } from '../../lib/api'
import type { CaseView, GaragePoint, GaragePreview } from '../../lib/api'

import './garages.css'

type NearbyGarage = GaragePreview['garages'][number]
type LocationCandidate = GaragePreview['location_candidates'][number]
const NO_GARAGES: NearbyGarage[] = []
const NO_CANDIDATES: LocationCandidate[] = []

function tooltip(text: string) {
  const label = document.createElement('span')
  label.textContent = text
  return label
}

function markerIcon(number: number, selected: boolean) {
  return L.divIcon({
    className: `garage-network-pin${selected ? ' is-selected' : ''}`,
    html: `<span>${number}</span>`,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  })
}

function GarageMap({ garages, origin, candidates, selectedId, onSelect, onSelectLocation }: {
  origin: GaragePoint | null
  candidates: LocationCandidate[]
  onSelectLocation: (point: GaragePoint) => void
  garages: NearbyGarage[]
  selectedId: string
  onSelect: (id: string) => void
}) {
  const container = useRef<HTMLDivElement>(null)
  const markers = useRef<Map<string, L.Marker>>(new Map())

  useEffect(() => {
    if (!container.current) return
    const first = origin || candidates[0] || garages[0]
    const center: L.LatLngExpression = first ? [first.latitude, first.longitude] : [46.6, 2.2]
    const map = L.map(container.current, { scrollWheelZoom: false }).setView(center, first ? 15 : 5)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
      maxZoom: 19,
      referrerPolicy: 'origin',
    }).addTo(map)
    const bounds = L.latLngBounds([])
    if (origin) {
      const point: L.LatLngExpression = [origin.latitude, origin.longitude]
      bounds.extend(point)
      L.marker(point, {
        title: 'Lieu de l’accident',
        zIndexOffset: 1000,
        icon: L.divIcon({ className: 'garage-accident-pin', html: '<span aria-hidden="true">!</span>', iconSize: [36, 36], iconAnchor: [18, 18] }),
      }).addTo(map).bindTooltip(tooltip('Accident'), { permanent: true, direction: 'top', offset: [0, -18] })
    }
    candidates.forEach((candidate, index) => {
      const point: L.LatLngExpression = [candidate.latitude, candidate.longitude]
      bounds.extend(point)
      L.marker(point, {
        title: `Adresse possible ${index + 1} : ${candidate.label}`,
        icon: L.divIcon({ className: 'garage-candidate-pin', html: `<span>${index + 1} ?</span>`, iconSize: [38, 38], iconAnchor: [19, 19] }),
      }).addTo(map).on('click', () => onSelectLocation({ latitude: candidate.latitude, longitude: candidate.longitude }))
        .bindTooltip(tooltip(candidate.label))
    })
    garages.forEach((garage, index) => {
      const point: L.LatLngExpression = [garage.latitude, garage.longitude]
      bounds.extend(point)
      const marker = L.marker(point, { icon: markerIcon(index + 1, garage.osm_id === selectedId) })
        .addTo(map).on('click', () => onSelect(garage.osm_id))
      marker.bindTooltip(tooltip(garage.name))
      markers.current.set(garage.osm_id, marker)
    })
    if (garages.length + candidates.length + (origin ? 1 : 0) > 1) map.fitBounds(bounds.pad(.2), { maxZoom: 15 })
    const resize = () => map.invalidateSize({ pan: false })
    const observer = new ResizeObserver(resize)
    observer.observe(container.current)
    requestAnimationFrame(resize)
    return () => { observer.disconnect(); markers.current.clear(); map.remove() }
  }, [garages, origin, candidates, onSelect, onSelectLocation])

  useEffect(() => {
    garages.forEach((garage, index) => markers.current.get(garage.osm_id)?.setIcon(markerIcon(index + 1, garage.osm_id === selectedId)))
  }, [garages, selectedId])

  return <div ref={container} className="garage-network-map" role="img"
    aria-label={origin ? `Carte du lieu de l’accident et des ${garages.length} garages à proximité` : candidates.length ? 'Carte des adresses possibles du lieu de l’accident' : 'Carte de recherche du lieu de l’accident'} />
}

export default function GaragePanel({ caseView, accessToken }: { caseView: CaseView; accessToken: string }) {
  const [preview, setPreview] = useState<GaragePreview | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [origin, setOrigin] = useState<GaragePoint | null>(null)
  const currentCase = useRef(caseView)
  currentCase.current = caseView
  const requestId = useRef(0)
  const onSelect = useCallback((id: string) => setSelectedId(id), [])
  const search = useCallback(async (origin: GaragePoint | null = null) => {
    const id = ++requestId.current
    setBusy(true); setError(''); setPreview(null); setOrigin(origin)
    try {
      const current = currentCase.current
      let result = await previewGarages(accessToken, current.id, current.state_version, origin)
      if (id !== requestId.current) return
      // Demo examples default to Paris, France when the address is ambiguous.
      const paris = result.status === 'needs_location' && result.location_candidates.find(candidate =>
        /(?:^|,)\s*Paris\s*(?:,|$)/i.test(candidate.label) && /(?:^|,)\s*France\s*(?:,|$)/i.test(candidate.label))
      if (paris) {
        const point = { latitude: paris.latitude, longitude: paris.longitude }
        setOrigin(point)
        result = await previewGarages(accessToken, current.id, currentCase.current.state_version, point)
        if (id !== requestId.current) return
      }
      setPreview(result)
      setOrigin(result.origin || origin)
      setSelectedId(result.garages[0]?.osm_id || '')
    } catch {
      if (id === requestId.current) setError('La recherche prend trop de temps ou le service est indisponible. Réessayez ou consultez les garages sur Google Maps.')
    } finally {
      if (id === requestId.current) setBusy(false)
    }
  }, [accessToken])
  useEffect(() => {
    void search()
    return () => { requestId.current++ }
  }, [search, caseView.id, caseView.intake.location])
  const onSelectLocation = useCallback((point: GaragePoint) => { void search(point) }, [search])
  const garages = preview?.garages || NO_GARAGES
  const candidates = preview?.status === 'needs_location' ? preview.location_candidates : NO_CANDIDATES
  const selected = garages.find(garage => garage.osm_id === selectedId) || garages[0]

  return <div className="garage-network" lang="fr" aria-busy={busy}>
    <p className="garage-network-intro">Autour de {caseView.intake.location || 'l’adresse du sinistre'}.</p>
    <GarageMap garages={garages} origin={origin} candidates={candidates} selectedId={selected?.osm_id || ''} onSelect={onSelect} onSelectLocation={onSelectLocation} />
    <p className="garage-map-legend">{origin ? <><span className="garage-accident-key" aria-hidden="true" /> Lieu de l’accident{garages.length > 0 && ' · Repères numérotés : garages'}</> : candidates.length ? 'Choisissez le lieu de l’accident sur la carte ou dans la liste.' : 'Localisation de l’accident en attente.'}</p>
    {busy && <p role="status">Recherche des garages à proximité…</p>}
    {error && <p className="ir-error" role="alert">{error}</p>}
    {preview?.status === 'needs_location' && <>
      <p>Précisez le lieu de recherche{preview.location_candidates.length ? ' en choisissant une adresse :' : ' dans la déclaration du dossier.'}</p>
      <div className="garage-location-options">{preview.location_candidates.map((candidate, index) => <button
        className="ir-button ir-secondary" key={index} type="button" onClick={() => void search({ latitude: candidate.latitude, longitude: candidate.longitude })}>
        {candidate.label}
      </button>)}</div>
    </>}
    {preview?.status === 'no_results' && <p className="garage-network-empty">Aucun garage trouvé à moins de {((preview.radius_m || 5000) / 1000).toLocaleString('fr-FR')} km de cette adresse.</p>}
    {selected && <>
      <p className="garage-network-intro"><strong>{garages.length} garage{garages.length > 1 ? 's' : ''} à proximité</strong> · distances à vol d’oiseau.</p>
      <div className="garage-network-list" aria-label="Garages sur la carte">
        {garages.map((garage, index) => <button key={garage.osm_id} type="button"
          className={garage.osm_id === selected.osm_id ? 'is-selected' : ''}
          aria-pressed={garage.osm_id === selected.osm_id} onClick={() => onSelect(garage.osm_id)}>
          <span className="garage-network-number">{index + 1}</span>
          <span><strong>{garage.name}</strong><small>{garage.address || 'Adresse non renseignée'}</small></span>
          <em>{garage.distance_m < 1000 ? `${garage.distance_m} m` : `${(garage.distance_m / 1000).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} km`}</em>
        </button>)}
      </div>
    </>}
    {error && <a className="ir-button ir-secondary garage-directions" href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(origin ? `garages près de ${origin.latitude},${origin.longitude}` : `garages ${caseView.intake.location || 'Paris'}`)}`} target="_blank" rel="noreferrer">Voir les garages sur Google Maps</a>}
    {!busy && <button className="ir-text-button" type="button" onClick={() => void search(origin)}>Relancer la recherche</button>}
    {preview && <p className="garage-network-source">Données : <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">{preview.attribution}</a></p>}
  </div>
}
