import type { AccidentJourneyView, RepairEstimate } from './api'
import { involvedVehicles } from './analysisPresentation'

type Assessment = NonNullable<AccidentJourneyView['review']>['assessment']

export type VehicleRepairs = {
  vehicle: string
  insured: boolean
  parts: string[]
  estimate: RepairEstimate | null
}

export function repairBreakdowns(assessment: Assessment): VehicleRepairs[] {
  const insured = assessment.insured_vehicle
  const vehicles = insured ? [insured] : involvedVehicles(assessment).map(plate => plate.vehicle)
  return vehicles.flatMap(vehicle => {
    const damages = assessment.media.damages.filter(damage => damage.vehicle === vehicle && damage.accident_link !== 'inconsistent')
    const parts = [...new Set(damages.flatMap(damage => damage.affected_parts))]
    // Each damage estimate covers its vehicle. Never add repeated views together.
    const estimates = damages.flatMap(damage => damage.estimate ? [damage.estimate] : [])
    const estimate = (vehicle === insured ? assessment.repair_estimate : null)
      ?? estimates.find(item => item.line_items?.length) ?? estimates[0] ?? null
    return estimate || parts.length ? [{ vehicle, insured: vehicle === insured, parts, estimate }] : []
  })
}

export function repairTotal(estimate: RepairEstimate) {
  if (!estimate.line_items?.length) return estimate
  return estimate.line_items.reduce((total, item) => ({
    minimum_minor: total.minimum_minor + item.minimum_minor,
    maximum_minor: total.maximum_minor + item.maximum_minor,
  }), { minimum_minor: 0, maximum_minor: 0 })
}
