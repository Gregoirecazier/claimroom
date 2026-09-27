// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { accidentJourney } from '../lib/accidentJourney.fixture'
import { repairBreakdowns, repairTotal } from '../lib/repairBreakdown'
import { RepairCostBreakdown } from './RepairCostBreakdown'

afterEach(cleanup)
const assessment = () => accidentJourney().review!.assessment

it('adds each damaged part once, with an equals sign and the sum in cents', () => {
  const value = assessment()
  value.repair_estimate!.line_items = [
    { label: 'Pare-chocs arrière', minimum_minor: 80000, maximum_minor: 150000 },
    { label: 'Feu arrière gauche', minimum_minor: 30000, maximum_minor: 70000 },
    { label: 'Aile arrière gauche', minimum_minor: 90000, maximum_minor: 180000 },
  ]
  render(<RepairCostBreakdown assessment={value} />)
  const table = screen.getByRole('table', { name: 'Postes de réparation du véhicule assuré' })
  expect([...table.querySelectorAll('tbody .aj-addition-sign')].map(item => item.textContent)).toEqual(['', '+', '+'])
  expect(table.querySelector('tfoot')!.textContent!.replace(/\s/g, '')).toBe('=Totalestimé2000€–4000€')
  expect(repairTotal(value.repair_estimate!)).toEqual({ minimum_minor: 200000, maximum_minor: 400000 })
})

it('shows available vehicle costs when the insured identity is still unknown', () => {
  const value = assessment()
  value.media.damages[0].estimate = value.repair_estimate
  value.repair_estimate = null
  value.insured_vehicle = null
  value.media.plates[0].role = 'unknown'
  render(<RepairCostBreakdown assessment={value} />)
  expect(screen.getByRole('table', { name: 'Postes de réparation : Peugeot' })).toBeTruthy()
  expect(screen.getByText('Véhicule assuré à confirmer')).toBeTruthy()
  expect(screen.queryByText('Estimation à compléter.')).toBeNull()
  expect(screen.queryByText('Montant proposé')).toBeNull()
})

it('does not replace missing insured costs with another vehicle’s estimate', () => {
  const value = assessment()
  value.media.damages.push({ ...value.media.damages[0], vehicle: 'BMW', estimate: value.repair_estimate })
  value.repair_estimate = null
  render(<RepairCostBreakdown assessment={value} />)
  expect(screen.getByText('Estimation à compléter.')).toBeTruthy()
  expect(screen.queryByRole('table')).toBeNull()
  expect(screen.queryByText('BMW')).toBeNull()
  expect(screen.getByText('À chiffrer')).toBeTruthy()
})

it('never adds estimates for overlapping views of the same vehicle', () => {
  const value = assessment()
  value.media.damages[0].estimate = value.repair_estimate
  value.media.damages.push(structuredClone(value.media.damages[0]))
  value.repair_estimate = null
  const repairs = repairBreakdowns(value)
  expect(repairs).toHaveLength(1)
  expect(repairTotal(repairs[0].estimate!)).toEqual({ minimum_minor: 200000, maximum_minor: 400000 })
})

it('keeps old global estimates without inventing a split or zero costs', () => {
  const value = assessment()
  delete value.repair_estimate!.line_items
  render(<RepairCostBreakdown assessment={value} />)
  expect(screen.getByText('Estimation globale')).toBeTruthy()
  expect(screen.getByText('Détail par poste non disponible.')).toBeTruthy()
  expect(screen.getByText('Pare-chocs')).toBeTruthy()
  expect(screen.getByText('À chiffrer')).toBeTruthy()
  expect(screen.queryByRole('table')).toBeNull()
})

it('keeps estimates for two vehicles in separate additions', () => {
  const value = assessment()
  value.media.damages[0].estimate = value.repair_estimate
  value.media.plates.push({ ...value.media.plates[0], vehicle: 'BMW', role: 'third_party' })
  value.media.damages.push({ ...value.media.damages[0], vehicle: 'BMW' })
  value.insured_vehicle = null
  value.repair_estimate = null
  render(<RepairCostBreakdown assessment={value} />)
  expect(screen.getAllByRole('table')).toHaveLength(2)
  expect(screen.getAllByRole('table').map(table => table.querySelector('tfoot')!.textContent!.replace(/\s/g, '')))
    .toEqual(['=Totalestimé2000€–4000€', '=Totalestimé2000€–4000€'])
})
