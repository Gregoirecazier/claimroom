import { useEffect, useState } from 'react'
import { ApiError, autoFakeSms, getSmsDepositLink } from '../lib/api'
import type { CaseView } from '../lib/api'

export function FollowUpSmsPanel({ caseView, accessToken, onUpdated }: {
  caseView: CaseView
  accessToken: string
  onUpdated: () => Promise<void>
}) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setUrl(''); setError('')
    void autoFakeSms(accessToken, caseView.id)
      .then(async message => {
        if (message.status !== 'delivered') throw new Error('Le message simulé n’est pas encore disponible.')
        const link = await getSmsDepositLink(accessToken, caseView.id, message.id)
        const token = new URL(link.url).hash.replace(/^#token=/, '')
        if (!token) throw new Error('Le lien du téléphone simulé est indisponible.')
        if (active) {
          setUrl(`/fake-whatsapp#token=${encodeURIComponent(token)}`)
          void onUpdated().catch(() => {})
        }
      })
      .catch(reason => {
        if (!active) return
        if (!(reason instanceof ApiError && reason.code === 'fake_whatsapp_not_configured')) {
          setError(reason instanceof Error ? reason.message : 'Le téléphone simulé est indisponible.')
        }
      })
    return () => { active = false }
  }, [accessToken, caseView.id])

  if (!url && !error) return null
  return <section className="ir-card ir-sms-panel" id="whatsapp" aria-label="Téléphone WhatsApp simulé">
    {url ? <a className="ir-button ir-primary" href={url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Ouvrir la conversation WhatsApp</a>
      : <p role="alert" className="ir-error">{error}</p>}
  </section>
}
