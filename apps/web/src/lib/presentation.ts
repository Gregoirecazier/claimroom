import type { CaseView } from './api'

const labels: Record<string, string> = {
  collecting: 'À compléter', review_ready: 'À valider', approved: 'Validé',
  registered: 'Prêt à transmettre', sent: 'Transmis · simulation',
  passed: 'Vérifié', needs_review: 'À vérifier', blocked: 'À compléter',
  ready: 'Terminée', running: 'En cours', stale: 'À relancer', failed: 'Échec',
  intake: 'Déclaration', counterparty: 'Assureur et destinataire', evidence: 'Pièces et analyse', approval: 'Validation humaine',
  scene_photo: 'Photo de la scène', vehicle_photo: 'Vue du véhicule', damage_photo: 'Détail des dégâts',
  vue_ensemble: 'Vue d’ensemble', detail_degats: 'Détail des dégâts', video_g1: 'Vidéo de l’accident',
  video_g2: 'Vidéo G2', video_g3: 'Vidéo G3',
  toyota_tiers_ensemble: 'Toyota tiers · vue d’ensemble', toyota_tiers_degats: 'Toyota tiers · dégâts',
  scene_video: 'Vidéo de l’accident', document: 'Document', other: 'Autre pièce',
  location: 'lieu', narrative: 'récit', incident_at: 'date du sinistre', vehicle_country: 'pays du véhicule',
  danger_status: 'danger immédiat', injury_status: 'blessures', insured_reference: 'référence du dossier',
  analysis_failed: 'Échec de l’analyse', analysis_stale: 'Analyse à actualiser',
  analysis_timeout_recovered: 'Analyse interrompue', draft_edited: 'Proposition modifiée',
  estimate_updated: 'Estimation modifiée', quote_attached: 'Devis associé', quote_removed: 'Devis retiré',
  report_line_edited: 'Compte rendu corrigé', camera_request_drafted: 'Demande de vidéo préparée',
  camera_request_status_changed: 'Suivi de la demande de vidéo',
  created: 'Dossier créé', intake_updated: 'Déclaration modifiée', evidence_added: 'Pièce ajoutée',
  g1_media_seeded: 'Photos et vidéo ajoutées', cctv_received: 'Images de surveillance ajoutées',
  analysis_completed: 'Analyse terminée', analysis_started: 'Analyse lancée', draft_updated: 'Proposition modifiée',
  draft_approved: 'Proposition validée', approval_revoked: 'Validation à renouveler',
  registration_simulated: 'Enregistrement simulé', send_simulated: 'Envoi simulé',
}

const reasons: Record<string, string> = {
  reported_immediate_danger_or_injury: 'Danger ou blessure signalé : reprise humaine nécessaire.',
  ambiguous_vehicle_or_coverage: 'Le véhicule tiers ou sa couverture reste à confirmer.',
  dated_matched_coverage_required: 'Confirmer l’assurance du tiers à la date du sinistre.',
  correspondent_query_mismatch: 'Le correspondant trouvé ne correspond pas à la recherche.',
  matched_correspondent_required: 'Identifier le correspondant de l’assureur tiers.',
  supported_proposition_required: 'Ajouter une preuve permettant de corroborer les faits.',
  contradictions_present: 'Clarifier les contradictions entre les sources.',
  missing_items_present: 'Compléter les informations ou pièces manquantes.',
  hypotheses_present: 'Vérifier les hypothèses de l’analyse.',
  quote_no_quote: 'Ajouter le devis de réparation.', quote_no_estimate: 'Renseigner l’estimation.',
  quote_mismatch: 'Le devis et l’estimation présentent un écart.', quote_outdated: 'Actualiser le devis après modification de l’estimation.',
  quote_attachment_required: 'Joindre le devis à la proposition.', estimate_draft_mismatch: 'Actualiser le montant de la proposition.',
  draft_required: 'Lancer l’analyse pour préparer la proposition.', upstream_gates_not_passed: 'Résoudre les points signalés avant validation.',
  unverified_recipient: 'Confirmer le destinataire.', draft_incomplete: 'Compléter la proposition.',
  draft_revision_stale: 'Relancer l’analyse après modification du dossier.', draft_digest_invalid: 'Recharger la proposition avant validation.',
  handler_approval_required: 'La proposition attend votre validation.',
}

export const displayLabel = (value: string) => labels[value] || value.replaceAll('_', ' ')
export const reasonLabel = (value: string) => reasons[value]
  || (value.startsWith('missing_') ? `À compléter : ${displayLabel(value.slice(8))}.` : 'Vérification complémentaire requise.')

export const hasCurrentAnalysis = (view: CaseView) => view.latest_analysis?.status === 'ready'
  && view.latest_analysis.input_content_revision === view.content_revision

export const hasCurrentApproval = (view: CaseView) => Boolean(view.current_draft && view.approval
  && !view.approval.superseded_at && view.approval.draft_id === view.current_draft.id
  && view.approval.draft_sha256 === view.current_draft.sha256
  && view.approval.approved_content_revision === view.content_revision)
