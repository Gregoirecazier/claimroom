import type { CaseView } from './api'
import { formatMoney } from './money'
import { displayLabel } from './presentation'

export type CaseSection = 'report' | 'evidence' | 'analysis'

export function sectionFromHash(hash: string): CaseSection {
  const target = hash.replace(/^#/, '')
  if (['report', 'whatsapp'].includes(target)) return 'report'
  if (['evidence', 'upload', 'cameras'].includes(target)) return 'evidence'
  if (['analysis', 'analysis-controls', 'estimate', 'sourced-report', 'vision', 'review', 'notifications', 'transmission-comment', 'transmission'].includes(target)) return 'analysis'
  return 'report'
}

export function nextCaseAction(view: CaseView): { title: string; label: string; target: string; urgent?: boolean } {
  if (view.voice_session?.status === 'urgent_human_handoff' || view.intake.danger_status === 'yes' || view.intake.injury_status === 'yes')
    return { title: 'Reprendre le dossier en priorité', label: 'Examiner la déclaration', target: 'report', urgent: true }
  if (view.actions.some(action => action.status === 'unknown'))
    return { title: 'Vérifier le résultat de la dernière transmission', label: 'Voir le suivi', target: 'analysis' }
  if (view.status === 'sent')
    return { title: 'Consulter le suivi de la décision', label: 'Voir la décision', target: 'analysis' }
  if (view.status === 'approved' || view.status === 'registered')
    return { title: 'Suivre les notifications de la décision', label: 'Voir le suivi', target: 'analysis' }
  if (view.intake.missing_fields.length)
    return { title: 'Compléter la déclaration de l’assuré', label: 'Reprendre la déclaration', target: 'report' }
  if (view.status === 'review_ready')
    return { title: 'Examiner l’analyse et valider la décision', label: 'Ouvrir la décision', target: 'analysis' }
  if (!view.evidence.some(item => item.mime_type.startsWith('image/')))
    return { title: 'Photos des dégâts attendues', label: 'Ajouter des photos', target: 'evidence' }
  if (view.evidence.filter(item => item.mime_type.startsWith('image/')).length < 2)
    return { title: 'Compléter les vues des dégâts', label: 'Ajouter des photos', target: 'evidence' }
  return { title: 'Examiner les faits et décider', label: 'Ouvrir l’analyse', target: 'analysis' }
}

export const formatCaseDate = (value: string | null) => value
  ? new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : 'À confirmer'
export const formatCaseAmount = (minor: number | null | undefined, currency = 'EUR') => minor == null
  ? 'À estimer' : formatMoney(minor, currency)

export function caseSummaryText(view: CaseView): string {
  return [
    `# ${view.intake.insured_reference || 'Dossier automobile'}`,
    'Dossier fictif · transmissions simulées',
    `Statut : ${displayLabel(view.status)}`,
    `Accident : ${formatCaseDate(view.intake.incident_at)}`,
    `Lieu : ${view.intake.location || 'À confirmer'}`,
    `Véhicule assuré : ${view.intake.insured_vehicle || 'À confirmer'} · ${view.intake.insured_plate || 'À confirmer'}`,
    '\n## Déclaration (non vérifiée)', view.intake.narrative,
    '\n## Compte rendu et sources',
    ...view.report.lines.map(line => [
      `- [${line.claim_kind}${line.stale ? ' · à actualiser' : ''}] ${line.text}`,
      line.uncertainty ? `  Incertitude : ${line.uncertainty}` : '',
      `  Sources : ${line.source_refs.map(ref => `${ref.kind} ${ref.id}${ref.locator ? ` (${ref.locator})` : ''}`).join(', ') || 'Aucune'}`,
    ].filter(Boolean).join('\n')),
    '\n## Réparations',
    `Estimation : ${formatCaseAmount(view.estimate?.total_minor)}`,
    ...(view.estimate?.line_items.map(item => `- ${item.label} : ${formatCaseAmount(item.amount_minor)}`) || []),
    `Devis : ${view.quote?.filename || 'Non associé'}`,
    '\n## Pièces', ...view.evidence.map(item => `- ${item.original_filename || displayLabel(item.kind)}`),
    '\n## Note de transmission', view.current_draft?.transmission_comment || 'Aucune note.',
  ].join('\n')
}
