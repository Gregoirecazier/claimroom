// Presentation fixtures only. These are not agent results or evaluation ground truth.
const at = '2026-09-25T10:00:00Z'
const scenes = [
  {
    key: 'g1', reference: 'CLM-2026-0843', name: 'Camille Martin', vehicle: 'Peugeot grise', plate: 'FR-482-KL',
    narrative: 'J’étais arrêté dans ma Peugeot. Une BMW noire a reculé et heurté mon arrière gauche, puis elle est repartie. Aucun blessé ni danger immédiat.',
    photos: ['photo-ensemble.png', 'photo-detail.png'],
    repairs: [['Pare-chocs arrière', 48000], ['Feu arrière gauche', 18000], ['Tôlerie', 26000], ['Peinture et fixations', 32000]],
    note: 'BMW AB12 CDE : arrière droit contre arrière gauche de la Peugeot. Synthèse de démonstration saisie manuellement.',
  },
  {
    key: 'g2', reference: 'CLM-2026-0844', name: 'Alex Moreau', vehicle: 'Renault Mégane grise', plate: 'GH-271-RM',
    narrative: 'Ma Renault était arrêtée. Une Opel noire a heurté l’avant gauche de ma voiture et l’a poussée à droite et légèrement en arrière. La plaque du tiers semble être RK18 LXP ou RK18 LYP ; je ne peux pas confirmer la fin.',
    photos: ['photo-renault-ensemble.png', 'photo-renault-detail.png'], repairs: [],
    note: 'Plaque Opel incertaine : RK18 LXP / LYP. La voiture bleue est un véhicule de fond non impliqué. Identification du tiers à confirmer.',
  },
  {
    key: 'g3', reference: 'CLM-2026-0845', name: 'Sam Laurent', vehicle: 'Citroën C3 gris clair', plate: 'GT-638-VN',
    narrative: 'L’avant gauche de ma Citroën a heurté le côté passager d’une Toyota LM21 RZT. La Toyota s’est légèrement décalée, puis est revenue près de sa position initiale.',
    photos: ['photo-toyota-ensemble.png', 'photo-toyota-detail.png'], repairs: [],
    note: 'Les deux photos montrent la Toyota, véhicule tiers. Aucun chiffrage de la Citroën assurée ne peut être déduit de ces photos. Responsabilité à examiner avant tout recours.',
  },
]

export function makeCases() {
  return scenes.map((scene, index) => {
    const id = `00000000-0000-4000-8000-00000000000${index + 1}`
    const evidence = [...scene.photos, `video-${scene.key}.mp4`].map((filename, n) => ({
      id: `${scene.key}-piece-${n}`, case_id: id, kind: ['vehicle_photo', 'damage_photo', 'scene_video'][n],
      role: n === 2 ? `video_${scene.key}` : scene.key === 'g3' ? ['toyota_tiers_ensemble', 'toyota_tiers_degats'][n] : ['vue_ensemble', 'detail_degats'][n],
      original_filename: filename, storage_path: `/demo-media/${scene.key}/${filename}`,
      mime_type: n === 2 ? 'video/mp4' : 'image/png', source_kind: `synthetic_${scene.key}`,
      mode: 'mock', checksum_status: 'client_declared', client_sha256: null, sha256_verified: null,
      received_at: at, display_order: n, byte_size: 0,
    }))
    const ready = scene.key === 'g1'
    if (ready) evidence.push({ id: 'quote-pdf', case_id: id, kind: 'document', role: null,
      original_filename: 'devis-demo.pdf', storage_path: '/demo-media/devis-demo.pdf', mime_type: 'application/pdf',
      source_kind: 'demo_fixture', mode: 'mock', checksum_status: 'client_declared', client_sha256: null,
      sha256_verified: null, received_at: at, display_order: 3, byte_size: 0 })
    const estimate = ready ? { id: `${scene.key}-estimate`, version: 1, total_minor: 124000,
      currency: 'EUR', tax_basis: 'TTC', estimate_source: 'demo_fixture',
      line_items: scene.repairs.map(([label, amount_minor], n) => ({ id: `repair-${n}`, label, amount_minor })) } : null
    return {
      id, created_by_user_id: 'demo', scenario_id: scene.key, synthetic: true, status: ready ? 'review_ready' : 'collecting',
      state_version: 3, content_revision: 2, created_at: at, updated_at: at,
      intake: { reported_at: at, incident_at: at, insured_reference: scene.reference,
        insured_name: scene.name, insured_vehicle: scene.vehicle, insured_plate: scene.plate,
        insured_identity_source: 'synthetic_fixture', policy_reference: null, time_source: 'caller_statement',
        location: '18 rue des Ateliers-Démo, Paris (lieu fictif)', vehicle_country: 'FR', narrative: scene.narrative,
        danger_status: 'no', injury_status: 'no', missing_fields: [] },
      evidence, camera_requests: [], provider_results: [], video_analyses: [], voice_session: null,
      latest_analysis: ready ? { id: 'demo-analysis', status: 'ready', input_content_revision: 2, output: null } : null,
      current_draft: ready ? { id: 'demo-draft', case_id: id, transmission_comment: '', created_at: at, version: 1, content_revision: 2, sha256: 'demo-initial',
        recipient: { name: 'Northbridge Demo Motor', organization: 'Assureur fictif', email: 'claims@example.test' },
        amount_minor: 124000, currency: 'EUR', attachment_ids: evidence.map(e => e.id),
        body: 'Madame, Monsieur,\n\nVeuillez trouver le compte rendu, les pièces du dossier et le devis de 1 240 € TTC.\n\nDémonstration : aucun assureur réel ne sera contacté.' } : null,
      gate_results: ready ? ['intake', 'counterparty', 'evidence', 'approval'].map(gate => ({
        gate, status: gate === 'approval' ? 'needs_review' : 'passed',
        reason_codes: gate === 'approval' ? ['handler_approval_required'] : [], source_refs: [],
      })) : [],
      approval: null, actions: [], estimate,
      quote: ready ? { id: 'demo-quote', version: 1, evidence_id: 'quote-pdf', filename: 'devis-demo.pdf',
        total_ttc_minor: 124000, amount_source: 'handler_entered', attached_estimate_version: 1 } : null,
      quote_status: ready ? 'matched' : 'no_estimate',
      timeline: [{ id: `${scene.key}-created`, event_type: 'case.created', occurred_at: at, state_version_after: 3 },
        { id: `${scene.key}-media`, event_type: 'case.g1_media_seeded', occurred_at: at, state_version_after: 3 }],
      report: { analysis_current: ready, needs_reanalysis: false, lines: [
        { id: 'declaration', text: scene.narrative, claim_kind: 'declaration', source_refs: [{ kind: 'intake', id, locator: 'narrative' }], uncertainty: null, as_of_revision: 2, stale: false, mode: null },
        { id: 'demo-observation', text: scene.note, claim_kind: 'hypothesis', source_refs: [{ kind: 'evidence', id: evidence[2].id, locator: 'video' }], uncertainty: 'Repère de présentation manuel ; ce texte n’est pas produit par un agent.', as_of_revision: 2, stale: false, mode: 'mock' },
      ] },
    }
  }).map(c => {
    if (c.current_draft) c.current_draft.package = {
      schema_version: 1, report_lines: structuredClone(c.report.lines), estimate: structuredClone(c.estimate),
      quote: structuredClone(c.quote), video_analyses: [],
    }
    return c
  })
}
