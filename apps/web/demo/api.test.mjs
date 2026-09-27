import { test } from 'node:test'
import assert from 'node:assert/strict'
import { makeCases } from './fixtures.mjs'
import { listCases, getCase, approveDraft, upsertEstimate, attachQuote, runAnalysis, simulateSend, simulateRegistration, getTransmissionPreview, updateCurrentDraft } from './api.mjs'

test('the preview has three distinct cases with their own vehicle roles and media', () => {
  const cases = makeCases()
  assert.deepEqual(cases.map(c => c.scenario_id), ['g1', 'g2', 'g3'])
  assert.equal(new Set(cases.map(c => c.intake.insured_plate)).size, 3)
  for (const c of cases) {
    assert.ok(c.evidence.every(e => e.case_id === c.id))
    assert.ok(c.evidence.filter(e => e.mime_type !== 'application/pdf').every(e => e.storage_path.includes(`/${c.scenario_id}/`)))
  }
  assert.match(cases[1].intake.narrative, /LXP ou RK18 LYP/)
  assert.ok(cases[2].evidence.filter(e => e.mime_type === 'image/png').every(e => e.role.startsWith('toyota_tiers')))
  assert.equal(cases[2].estimate, null)
  assert.equal(cases[2].current_draft, null)
})

test('changing an estimate revokes approval; reassociation handles mismatch and needs a fresh analysis', async () => {
  const [initial] = await listCases()
  const approved = await approveDraft('', initial.id, initial.current_draft.id, initial.current_draft.sha256, initial.state_version)
  const changed = await upsertEstimate('', initial.id, approved.state_version, [{ id: 'repair', label: 'Réparation', amount_minor: 130000 }], 130000)
  assert.equal(changed.approval, null)
  assert.equal(changed.quote_status, 'outdated')
  await assert.rejects(simulateSend('', initial.id, changed.state_version, 'no-approval'))
  await assert.rejects(attachQuote('', initial.id, changed.state_version, 'g2-piece-0', 130000))
  const mismatch = await attachQuote('', initial.id, changed.state_version, 'quote-pdf', 129000)
  assert.equal(mismatch.quote_status, 'mismatch')
  const aligned = await attachQuote('', initial.id, mismatch.state_version, 'quote-pdf', 130000)
  assert.equal(aligned.quote_status, 'matched')
  assert.equal(aligned.current_draft, null)
  const analyzed = await runAnalysis('', initial.id, aligned.state_version)
  assert.equal(analyzed.case.current_draft.amount_minor, 130000)
  assert.equal(analyzed.case.approval, null)
  const replay = await attachQuote('', initial.id, analyzed.case.state_version, 'quote-pdf', 130000)
  assert.equal(replay.state_version, analyzed.case.state_version)
  assert.equal((await getCase('', initial.id)).quote.total_ttc_minor, 130000)
})

test('G2 and G3 remain blocked in the preview instead of inventing a recovery package', async () => {
  for (const c of (await listCases()).slice(1)) {
    const result = await runAnalysis('', c.id, c.state_version)
    assert.equal(result.case.current_draft, null)
    assert.equal(result.case.status, 'collecting')
    await assert.rejects(simulateSend('', c.id, result.case.state_version, 'blocked'))
  }
})


test('the preview binds approval to the draft digest and freezes the sent comment in the receipt', async () => {
  const initial = (await listCases())[0]
  await assert.rejects(approveDraft('', initial.id, initial.current_draft.id, 'obsolete-digest', initial.state_version))
  const changed = await updateCurrentDraft('', initial.id, initial.state_version, { transmission_comment: 'Pièces revues.' })
  const preview = await getTransmissionPreview('', initial.id)
  assert.equal(preview.transmission_comment, 'Pièces revues.')
  assert.equal(preview.package.estimate.total_minor, changed.estimate.total_minor)
  const approved = await approveDraft('', changed.id, changed.current_draft.id, changed.current_draft.sha256, changed.state_version)
  await simulateRegistration('', changed.id, approved.state_version, 'register')
  const registered = await getCase('', changed.id)
  const receipt = await simulateSend('', changed.id, registered.state_version, 'send')
  assert.equal(receipt.envelope.transmission_comment, 'Pièces revues.')
  assert.equal(receipt.envelope.draft_sha256, changed.current_draft.sha256)
  const replay = await simulateSend('', changed.id, registered.state_version, 'send')
  assert.equal(replay.id, receipt.id)
})
