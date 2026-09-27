import type { AccidentJourneyView } from './api'
export function accidentJourney(): AccidentJourneyView {
  const citation = { evidence_id: 'photo', timestamp_seconds: null }
  return { content_revision: 2, model: 'gpt-6-astra', catalogue_count: 3, notification_mode: 'simulated', notifications: [],
    review: { id: 'review-id', status: 'awaiting_review', blockers: [], model: 'gpt-6-astra', approved_amount_minor: null, amendment_reason: null,
      insurance_matches: [{ vehicle: 'BMW', plate: 'AB12 CDE', status: 'matched', data: { insurer_name: 'Northbridge Demo', driver_name: 'Conducteur fictif UK 0001' } }],
      assessment: { selected_video_id: 'video-id', match_reasoning: 'Les impacts et véhicules concordent.', insured_vehicle: 'Peugeot', at_fault_vehicle: 'BMW', missing_information: [],
        video_candidates: [{ video_id: 'video-id', compatibility: 'strong', reasons: 'Même véhicule.' }, { video_id: 'other', compatibility: 'incompatible', reasons: 'Autre impact.' }],
        repair_estimate: { minimum_minor: 200000, maximum_minor: 400000, currency: 'EUR', assumptions: 'Pièces et main-d’œuvre, dégâts cachés exclus.',
          line_items: [{ label: 'Remplacement du pare-chocs', minimum_minor: 110000, maximum_minor: 220000 },
            { label: 'Préparation et peinture', minimum_minor: 50000, maximum_minor: 100000 },
            { label: 'Dépose et remontage', minimum_minor: 40000, maximum_minor: 80000 }] },
        media: { analyzed_evidence_ids: ['photo','video-id'], summary: 'La BMW heurte la Peugeot.', observations: [],
          plates: [{ vehicle: 'Peugeot', plate: 'FR-482-KL', role: 'insured', legibility: 'readable', confidence: 'high', description: 'Plaque visible.', citations: [citation] }],
          damages: [{ vehicle: 'Peugeot', description: 'Pare-chocs enfoncé.', affected_parts: ['Pare-chocs'], severity: 'moderate', accident_link: 'consistent', estimate: null, confidence: 'high', citations: [citation] }],
          liability: { likely_responsible: 'third_party', reasoning: 'La BMW recule vers le véhicule immobile.', confidence: 'high', citations: [citation], limitations: ['Hypothèse à valider.'], requires_human_review: true, vehicle_assessments: [] },
          cross_evidence_consistency: 'Dommages cohérents.', limitations: [] }
      }
    }
  }
}
