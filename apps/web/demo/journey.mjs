// Hand-authored UI examples. These are never presented as a fresh AI assessment.
export function exampleJourney(c) {
  if (c.scenario_id === 'waiting' && (c.intake.missing_fields.length || c.evidence.filter(e => e.mime_type.startsWith('image/')).length < 2)) return {
    content_revision: c.content_revision, model: 'fixture-ui', catalogue_count: 3,
    notification_mode: 'simulated', notification_preview: [], notifications: [], review: null,
  }
  const ready = c.scenario_id === 'g1' || c.scenario_id === 'waiting'
  const video = c.evidence.find(e => e.mime_type.startsWith('video/'))
  const photo = c.evidence.find(e => e.mime_type.startsWith('image/'))
  const source = [{ evidence_id: video?.id || photo?.id || 'demo-video', timestamp_seconds: video ? 2.375 : null }]
  const insured = c.intake.insured_vehicle
  const third = ready ? 'BMW gris foncé' : c.scenario_id === 'g2' ? 'Opel noire' : 'Toyota grise'
  const estimate = ready ? {
    minimum_minor: 350000, maximum_minor: 700000, currency: 'EUR',
    assumptions: 'Exemple préparé pour la revue de l’interface. Le démontage et le devis du réparateur restent nécessaires pour confirmer les dommages cachés.',
    line_items: [
      { label: 'Feu arrière gauche', minimum_minor: 45000, maximum_minor: 85000 },
      { label: 'Aile et logement du feu', minimum_minor: 110000, maximum_minor: 230000 },
      { label: 'Pare-chocs et fixations', minimum_minor: 80000, maximum_minor: 160000 },
      { label: 'Peinture et main-d’œuvre', minimum_minor: 115000, maximum_minor: 225000 },
    ],
  } : null
  const blockers = ready ? [] : c.scenario_id === 'g2'
    ? ['La plaque du tiers reste ambiguë : RK18 LXP ou RK18 LYP. Une vue plus nette est nécessaire.']
    : ['Les photos montrent le véhicule tiers. Ajoutez une vue d’ensemble et les dommages de la Citroën assurée.']
  return {
    content_revision: c.content_revision, model: 'fixture-ui', catalogue_count: 3, notification_mode: 'simulated',
    notification_preview: [
      { channel: 'sms', recipient: '+33 6 •• •• •• 00', mode: 'simulated', status: 'ready', reason: null, subject: null },
      { channel: 'email', recipient: 'sinistres@example.test', mode: 'simulated', status: 'ready', reason: null, subject: `Dossier ${c.intake.insured_reference}`, body: 'Compte rendu et proposition de recours du dossier fictif.' },
    ],
    notifications: [],
    review: {
      id: `preview-review-${c.id}-${c.content_revision}`, status: ready ? 'awaiting_review' : 'needs_information',
      blockers, model: 'fixture-ui', approved_amount_minor: null, amendment_reason: null,
      insurance_matches: [
        { vehicle: insured, plate: c.intake.insured_plate, status: 'matched', data: { insurer_name: 'Azur Démo Auto', driver_name: c.intake.insured_name, insurer_email: 'gestionnaire@example.test' } },
        { vehicle: third, plate: ready ? 'AB12 CDE' : null, status: ready ? 'matched' : 'ambiguous', data: ready ? { insurer_name: 'Northbridge Démo Motor', insurer_email: 'sinistres@example.test' } : {} },
      ],
      assessment: {
        selected_video_id: video?.id || null, match_reasoning: 'Vidéo synthétique associée aux photos de cet exemple.',
        insured_vehicle: insured, at_fault_vehicle: ready ? third : null, involved_vehicles: [insured, third],
        key_facts: ready ? ['La Peugeot est à l’arrêt au bord de la chaussée.', 'La BMW recule et heurte l’arrière gauche de la Peugeot.', 'Les dommages visibles sur les photos sont cohérents avec ce contact.'] : [c.intake.narrative],
        video_candidates: [], repair_estimate: estimate, missing_information: blockers,
        media: {
          analyzed_evidence_ids: c.evidence.map(e => e.id), summary: c.intake.narrative,
          observations: [],
          plates: [
            { vehicle: insured, plate: c.intake.insured_plate, role: 'insured', legibility: 'clear', description: 'Véhicule assuré visible sur la scène.', confidence: 'high', citations: source },
            { vehicle: third, plate: ready ? 'AB12 CDE' : null, role: 'third_party', legibility: ready ? 'clear' : 'partial', description: 'Véhicule tiers impliqué dans le contact.', confidence: ready ? 'high' : 'low', citations: source },
          ],
          damages: [{ vehicle: insured, description: ready ? 'Feu arrière gauche cassé, aile et pare-chocs déformés. Un contrôle après démontage doit préciser les dommages aux supports.' : 'Dommages du véhicule assuré à documenter.',
            affected_parts: ready ? ['Feu arrière gauche', 'Aile arrière gauche', 'Pare-chocs arrière'] : [], severity: ready ? 'severe' : 'unknown', accident_link: ready ? 'consistent' : 'unknown', estimate,
            confidence: ready ? 'medium' : 'low', citations: photo ? [{ evidence_id: photo.id, timestamp_seconds: null }] : [] }],
          liability: { likely_responsible: third, reasoning: 'La manœuvre en marche arrière provoque le contact avec le véhicule arrêté.', confidence: 'medium', citations: source,
            limitations: ['L’analyse visuelle ne remplace pas la décision du gestionnaire.'], requires_human_review: true,
            vehicle_assessments: [
              { vehicle: insured, role: 'insured', assessment: 'no_visible_contribution', reasoning: 'Véhicule à l’arrêt.', confidence: 'high', citations: source },
              { vehicle: third, role: 'third_party', assessment: ready ? 'likely_responsible' : 'undetermined', reasoning: ready ? 'Contact lors de la marche arrière.' : 'Éléments insuffisants.', confidence: 'medium', citations: source },
            ],
          },
          cross_evidence_consistency: ready ? 'Photos et vidéo concordantes.' : 'Vérification complémentaire nécessaire.',
          limitations: ['Les dommages cachés et l’alignement de la structure restent à vérifier par le réparateur.'],
        },
      },
    },
  }
}

export function exampleVoice(c) {
  return { provider: 'vapi', session_id: `preview-call-${c.id}`, mode: 'mock', telephony_provider: 'web',
    status: 'complete', missing_p0: [], reason_codes: [], call_started_at: c.created_at,
    recording: { status: 'unavailable', mime_type: null, byte_size: null, sha256: null, error_code: null },
    facts: ['insured_name', 'location', 'narrative'].map(field => ({ field, value: c.intake[field], excerpt: c.intake[field], uncertainty: 'explicit' })),
    segments: [
      { id: 'opening', speaker: 'assistant', text: 'Bonjour, comment puis-je vous aider ?', start_ms: 0, end_ms: 2300 },
      { id: 'caller', speaker: 'caller', text: `Bonjour, je suis ${c.intake.insured_name}. ${c.intake.narrative} C’était au ${c.intake.location}.`, start_ms: 2500, end_ms: 22000 },
      { id: 'closing', speaker: 'assistant', text: 'Votre déclaration est enregistrée. Vous recevrez un lien pour vérifier les informations et joindre vos photos.', start_ms: 22500, end_ms: 28500 },
    ],
  }
}
