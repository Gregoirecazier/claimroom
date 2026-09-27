import { test } from 'node:test'
import assert from 'node:assert/strict'
import { claimSms } from './claimSms.mjs'

test('SMS explains the proposed amount with line midpoints and approved garages', () => {
  const caseView = {
    intake: { insured_name: 'Camille', insured_reference: 'CLM-2026-0843', insured_vehicle: 'Peugeot grise' },
    partner_garages: [
      { name: 'Atelier République', distance_m: 720, network_status: 'approved' },
      { name: 'Garage inconnu', distance_m: 900, network_status: 'unverified' },
    ],
  }
  const assessment = { repair_estimate: { minimum_minor: 350000, maximum_minor: 700000, line_items: [
    { label: 'Feu arrière gauche', minimum_minor: 45000, maximum_minor: 85000 },
    { label: 'Aile et logement du feu', minimum_minor: 110000, maximum_minor: 230000 },
    { label: 'Pare-chocs et fixations', minimum_minor: 80000, maximum_minor: 160000 },
    { label: 'Peinture et main-d’œuvre', minimum_minor: 115000, maximum_minor: 225000 },
  ] } }
  const sms = claimSms(caseView, assessment, 525000)
  for (const text of ['Camille', 'CLM-2026-0843', '5 250', '650', '1 700', '1 200', 'Atelier République (720 m)']) assert.ok(sms.includes(text), text)
  assert.doesNotMatch(sms, /Garage inconnu|rembours|pris en charge/)
  assert.match(claimSms({ ...caseView, partner_garages: [] }, assessment, 500000), /montant retenu/)
})
