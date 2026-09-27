import { expect, it } from 'vitest'
import { accidentJourney } from './accidentJourney.fixture'
import { concisePoints, involvedVehicles, vehicleName } from './analysisPresentation'

it('does not fill an unknown accident pair with background cars or readable plates', () => {
  const assessment = accidentJourney().review!.assessment
  assessment.insured_vehicle = null
  assessment.at_fault_vehicle = null
  assessment.media.plates[0].role = 'unknown'
  assessment.media.damages[0].accident_link = 'uncertain'
  assessment.media.liability.vehicle_assessments = [{ vehicle: 'Peugeot', role: 'unknown', assessment: 'no_visible_contribution', reasoning: 'Voiture stationnée en arrière-plan.', confidence: 'low', citations: [] }]
  expect(involvedVehicles(assessment)).toEqual([])
})

it('uses the explicit pair despite plate order and damage uncertainty', () => {
  const assessment = accidentJourney().review!.assessment
  const plate = assessment.media.plates[0]
  assessment.media.plates = [
    { ...plate, vehicle: 'Passant', role: 'unknown' },
    { ...plate, vehicle: 'BMW', role: 'unknown' },
    { ...plate, role: 'unknown' },
  ]
  assessment.involved_vehicles = ['Peugeot', 'BMW']
  expect(involvedVehicles(assessment).map(item => item.vehicle)).toEqual(['Peugeot', 'BMW'])
})

it('keeps complete negative and uncertain statements instead of cutting their qualifications', () => {
  expect(concisePoints('Le choc semble latéral, mais le point de contact est masqué. Aucun dégât visible ne permet de confirmer cette hypothèse.')).toEqual([
    'Le choc semble latéral, mais le point de contact est masqué.',
    'Aucun dégât visible ne permet de confirmer cette hypothèse.',
  ])
  expect(vehicleName('Peugeot argentée — FR-482-KL', 'FR-482-KL')).toBe('Peugeot argentée')
})
