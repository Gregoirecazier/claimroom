import type { CaseSummary } from './api'

export function caseIdFromPath(path: string): string | null {
  return path.match(/^\/cases\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i)?.[1] ?? null
}

export function normalizeSearch(value: string): string {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('fr-FR').replace(/[\s-]+/g, '')
}

export function filterCases(cases: CaseSummary[], query: string): CaseSummary[] {
  const normalized = normalizeSearch(query)
  if (!normalized) return cases
  return cases.filter(item => [
    item.intake.insured_reference,
    item.intake.insured_name,
    item.intake.insured_vehicle,
    item.intake.insured_plate,
    item.intake.location,
    item.scenario_id,
  ].some(value => value && normalizeSearch(value).includes(normalized)))
}
