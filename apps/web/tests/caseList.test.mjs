import assert from 'node:assert/strict'
import test from 'node:test'
import { caseIdFromPath, filterCases, normalizeSearch } from '../src/lib/caseList.ts'

const g1 = { id: '00000000-0000-4000-8000-000000000001', scenario_id: 'g1', intake: {
  insured_reference: 'CLM-2026-0842', insured_name: 'Camille Martin', insured_vehicle: 'Peugeot grise', insured_plate: 'FR-482-KL', location: 'Paris',
} }
const complete = { id: '00000000-0000-4000-8000-000000000002', scenario_id: 'complete', intake: {
  insured_reference: 'CLAIM-SYN-1042', insured_name: null, insured_vehicle: null, insured_plate: null, location: 'Lille',
} }

test('search finds G1 by name, reference and plate after accent/space/hyphen normalization', () => {
  assert.equal(normalizeSearch(' Cámille - Martin '), 'camillemartin')
  for (const query of ['Camille', 'cámille martin', 'CLM 2026 0842', 'fr482kl', 'FR-482-KL']) {
    assert.deepEqual(filterCases([g1, complete], query).map(item => item.id), [g1.id])
  }
  assert.deepEqual(filterCases([g1, complete], 'CLAIM-SYN-1042').map(item => item.id), [complete.id])
})

test('direct case routes retain the UUID and reject malformed paths', () => {
  assert.equal(caseIdFromPath(`/cases/${g1.id}`), g1.id)
  assert.equal(caseIdFromPath(`/cases/${complete.id}/`), complete.id)
  assert.equal(caseIdFromPath('/'), null)
  assert.equal(caseIdFromPath('/cases/not-an-id'), null)
})
