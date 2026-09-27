import { useEffect, useState } from 'react'
import { getSmsLink, listSmsLinks } from '../lib/api'
import type { CaseView } from '../lib/api'

export function SmsDepositPanel({ caseView, accessToken }: {
  caseView: CaseView
  accessToken: string
}) {
  const [url, setUrl] = useState('')

  useEffect(() => {
    let active = true
    setUrl('')
    void (async () => {
      const messages = await listSmsLinks(accessToken, caseView.id)
      const delivered = messages
        .filter(message => message.status === 'delivered')
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
      for (const message of delivered) {
        try {
          const link = await getSmsLink(accessToken, caseView.id, message.id)
          if (active) setUrl(link.url)
          return
        } catch { /* An older delivered link may still be valid. */ }
      }
    })().catch(() => { /* No private SMS chat is available for this case. */ })
    return () => { active = false }
  }, [accessToken, caseView.id, caseView.state_version])

  if (!url) return null
  return <section className="ir-card ir-sms-panel" aria-label="Chat privé du dossier">
    <a className="ir-button ir-primary" href={url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Ouvrir le chat privé</a>
  </section>
}
