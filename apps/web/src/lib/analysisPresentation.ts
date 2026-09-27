import type { AccidentJourneyView } from './api'

type Assessment = NonNullable<AccidentJourneyView['review']>['assessment']

// Existing reports contain every visible car. Select by accident evidence, never
// by array position or by a legible plate alone; unknown insurance roles are valid.
export function involvedVehicles(assessment: Assessment) {
  const plates = [...new Map(assessment.media.plates.map(plate => [plate.vehicle, plate])).values()]
  if (assessment.involved_vehicles?.length) {
    return assessment.involved_vehicles.slice(0, 2)
      .flatMap(name => plates.filter(plate => plate.vehicle === name))
  }
  return plates.map(plate => {
    const liability = assessment.media.liability.vehicle_assessments?.find(item => item.vehicle === plate.vehicle)
    const damage = assessment.media.damages.find(item => item.vehicle === plate.vehicle)
    let score = 0
    if (damage?.accident_link === 'consistent') score = 1
    if (['likely_responsible', 'possibly_contributing'].includes(liability?.assessment || '')) score = 2
    if (plate.role === 'insured' || plate.role === 'third_party') score = 3
    if (plate.vehicle === assessment.at_fault_vehicle) score = 4
    if (plate.vehicle === assessment.insured_vehicle) score = 5
    return { plate, score }
  }).filter(item => item.score > 0).sort((a, b) => b.score - a.score).slice(0, 2).map(item => item.plate)
}

// Legacy prose is rendered as complete sentences, without technical commentary.
// New analyses supply a bounded key_facts array directly.
export function concisePoints(text: string, maximum = 2): string[] {
  const sentences = text.split(/\n+|(?<=[.!?])\s+(?=[A-ZÀ-Ÿ])/u)
    .map(sentence => sentence.replace(/^\s*[-*•]\s*/, '').trim())
    .filter(sentence => sentence && !/\b(?:repair_estimate|at_fault_vehicle|insured_vehicle|undetermined)\b/u.test(sentence))
    .map(sentence => sentence.replace(/^Dans la vidéo (?:synthétique )?(?:retenue|sélectionnée),\s*/iu, ''))
    .map(sentence => sentence.charAt(0).toLocaleUpperCase('fr-FR') + sentence.slice(1))
  return [...new Set(sentences)].slice(0, maximum)
}

export function vehicleName(vehicle: string, plate: string | null) {
  if (!plate) return vehicle
  return vehicle.replace(plate, '').replace(/\s*[—–·-]\s*$/, '').trim() || vehicle
}

export function blockerLabel(text: string) {
  const labels: Record<string, string> = {
    'Aucune vidéo ne correspond avec suffisamment de certitude aux photos.': 'Vidéo correspondante à confirmer.',
    'Le véhicule assuré et une estimation de ses dommages doivent être établis.': 'Véhicule assuré et chiffrage à confirmer.',
    'La responsabilité d’un tiers doit être établie avant de préparer son recours.': 'Responsabilité du tiers à confirmer.',
    'La plaque lisible du tiers doit correspondre à un assureur dans la base fictive.': 'Assureur du tiers à identifier à partir de sa plaque.',
  }
  return labels[text] || text
}
