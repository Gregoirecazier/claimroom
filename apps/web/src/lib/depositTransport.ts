const apiBase = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export class GuestRequestError extends Error {
  constructor(public code: string, message: string) { super(message) }
}
export async function guestRequest<T>(path: string, token: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...options, cache: 'no-store', referrerPolicy: 'no-referrer',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...options.headers },
  })
  if (!response.ok) {
    const data = await response.json().catch(() => null) as { error?: { code?: string }; detail?: { code?: string } } | null
    const code = data?.error?.code || data?.detail?.code || `http_${response.status}`
    const message: Record<string, string> = {
      expired_grant: 'Ce lien a expiré. Demandez un nouveau lien au gestionnaire.',
      revoked_grant: 'Ce lien a été retiré. Contactez votre gestionnaire.',
      expired_session: 'Votre session a expiré. Rouvrez le lien reçu par SMS.',
      invalid_grant: 'Ce lien n’est pas valide. Vérifiez le SMS reçu.',
      invalid_session: 'Rouvrez le lien reçu par SMS.',
      stale_case: 'Le dossier a changé. Réessayez.',
      stale_chat_history: 'La conversation a changé dans un autre onglet. Rouvrez le lien pour retrouver la dernière version.',
      case_read_only: 'Ce dossier est déjà enregistré ou envoyé. Contactez votre gestionnaire.',
      forbidden_capability: 'Cette action n’est pas disponible avec ce lien.',
      invalid_upload: 'Ce fichier ne peut pas être ajouté. Vérifiez son format et sa taille.',
      storage_unavailable: 'Le dépôt de fichiers est momentanément indisponible.',
      chat_unavailable: 'L’assistant est momentanément indisponible.',
    }
    throw new GuestRequestError(code, message[code] || 'Action impossible. Réessayez.')
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}
