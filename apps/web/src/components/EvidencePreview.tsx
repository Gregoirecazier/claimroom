import { useEffect, useState } from 'react'
import type { Evidence, EvidenceReadUrl } from '../lib/api'

type Props = {
  evidence: Evidence
  initial: EvidenceReadUrl
  renew: () => Promise<EvidenceReadUrl>
  onClose: () => void
}

export function EvidencePreview({ evidence, initial, renew, onClose }: Props) {
  const [signed, setSigned] = useState(initial)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function refresh() {
    setLoading(true)
    try {
      setSigned(await renew())
      setError('')
    } catch {
      setError('Lecture privée indisponible. Réessayez de renouveler le lien.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const delay = Math.max(0, Date.parse(signed.expires_at) - Date.now() - 30_000)
    const timer = window.setTimeout(() => void refresh(), delay)
    return () => window.clearTimeout(timer)
  }, [signed.expires_at])

  return <div className="ir-preview-backdrop" role="presentation" onClick={onClose}>
    <div className="ir-preview" role="dialog" aria-modal="true" aria-label="Aperçu de pièce" onClick={event => event.stopPropagation()}>
      <button className="ir-button ir-secondary" onClick={onClose}>Fermer</button>
      {evidence.mime_type.startsWith('video/')
        ? <video key={signed.url} controls src={signed.url} onError={() => setError('Lecture vidéo interrompue. Renouvelez le lien.')} />
        : <img src={signed.url} alt="Pièce du dossier" onError={() => setError('Image indisponible. Renouvelez le lien.')} />}
      <p>{evidence.role || evidence.kind} · lien privé temporaire</p>
      {error && <p className="ir-error" role="alert">{error}</p>}
      <button className="ir-button ir-secondary" disabled={loading} onClick={() => void refresh()}>
        {loading ? 'Renouvellement…' : 'Renouveler le lien'}
      </button>
    </div>
  </div>
}
