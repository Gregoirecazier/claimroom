import { expect, it } from 'vitest'
import type { CaseView } from './api'
import { reviewableCase } from './caseJourney.fixture'
import { caseSummaryText, nextCaseAction, sectionFromHash } from './caseJourney'


it('prioritizes current intake, photos, and handler review', () => {
  const view = reviewableCase()
  expect(nextCaseAction(view).target).toBe('analysis')
  view.status = 'collecting'
  view.evidence = []
  expect(nextCaseAction(view).target).toBe('evidence')
  view.intake.missing_fields = ['location']
  expect(nextCaseAction(view).target).toBe('report')
  view.intake.danger_status = 'yes'
  expect(nextCaseAction(view).urgent).toBe(true)
})

it('does not let an incomplete historical call override corrected intake', () => {
  const view = reviewableCase()
  if (view.voice_session) view.voice_session.status = 'incomplete'
  view.intake.missing_fields = []
  view.status = 'review_ready'
  expect(nextCaseAction(view).target).toBe('analysis')
})

it('keeps missing checks in analysis and unknown transmissions in the follow-up', () => {
  const view = reviewableCase()
  view.gate_results = []
  expect(nextCaseAction(view).target).toBe('analysis')
  view.actions = [{ status: 'unknown', kind: 'send' }] as CaseView['actions']
  expect(nextCaseAction(view).title).toContain('Vérifier le résultat')
})

it.each([
  ['#estimate', 'analysis'], ['#sourced-report', 'analysis'], ['#vision', 'analysis'],
  ['#cameras', 'evidence'], ['#whatsapp', 'report'], ['#review', 'analysis'], ['#transmission-comment', 'analysis'],
  ['#transmission', 'analysis'], ['#chronologie', 'report'], ['#notes', 'report'], ['#upload', 'evidence'], ['#notifications', 'analysis'],
])('opens the right workspace for deep link %s', (hash, section) => {
  expect(sectionFromHash(hash)).toBe(section)
})

it('exports the actual account with uncertainty and sources, without fabricating liability', () => {
  const view = reviewableCase()
  view.report.lines = [{ text: 'Plaque incertaine', claim_kind: 'hypothesis', uncertainty: 'LXP ou LYP', stale: true, source_refs: [{ kind: 'evidence', id: 'video', locator: '00:02' }] }] as CaseView['report']['lines']
  const output = caseSummaryText(view)
  expect(output).toContain('LXP ou LYP')
  expect(output).toContain('à actualiser')
  expect(output).toContain('evidence video (00:02)')
  expect(output).toContain('FR-482-KL')
  expect(output).not.toContain('100%')
})
