import type { CaseView, MediaAnalysis } from './api'

export function currentMediaAnalysis(view: CaseView): MediaAnalysis | null {
  const run = view.latest_analysis
  return run?.status === 'ready' && run.input_content_revision === view.content_revision
    && /^gemini-joint-media-v[12]:/.test(run.method_version || '') ? run.output?.media_analysis || null : null
}

export const mediaAnalysisErrors: Record<string, string> = {
  gemini_rate_limited: 'Le quota ou la fréquence d’appels Gemini est dépassé. Les pièces sont conservées. Vérifiez le quota et la facturation du projet Google si le problème persiste.',
  gemini_not_configured: 'La clé Gemini doit être configurée sur le serveur.',
  gemini_auth_failed: 'La clé Gemini du serveur est refusée.',
  gemini_access_denied: 'Le projet Google n’a pas accès à ce modèle Gemini.',
  gemini_resource_not_found: 'Le modèle ou un fichier Gemini est indisponible. Vérifiez la configuration du serveur.',
  gemini_temporarily_unavailable: 'Gemini est temporairement indisponible. Les pièces sont conservées.',
  gemini_unavailable: 'Gemini ne répond pas. Les pièces sont conservées.',
  incomplete_media_analysis: 'Le rapport ne couvre pas toutes les pièces ou tous les véhicules. Une nouvelle analyse est nécessaire.',
  gemini_invalid_response: 'Le rapport reçu est incomplet ou invalide. Une nouvelle analyse est nécessaire.',
  gemini_invalid_request: 'Gemini refuse la demande d’analyse. Vérifiez la configuration du serveur.',
  media_storage_unavailable: 'Les pièces ne sont pas accessibles au service d’analyse.',
  analysis_timeout: 'L’analyse a dépassé son délai. Essayez avec des extraits vidéo plus courts.',
}

export function mediaAnalysisStatus(view: CaseView): string {
  if (currentMediaAnalysis(view)) return 'Analyse Gemini disponible'
  const run = view.latest_analysis
  if (run?.input_content_revision === view.content_revision) {
    if (run.status === 'running') return 'Analyse Gemini en cours…'
    if (run.status === 'failed') return `Analyse à relancer : ${mediaAnalysisErrors[run.error_code || ''] || run.error_message || 'service indisponible'}`
  }
  return 'Analyse en attente'
}
