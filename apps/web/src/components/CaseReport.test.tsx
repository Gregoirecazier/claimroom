// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { CaseView, Evidence, ProviderResult, ReportLine } from '../lib/api'
import { CaseReport, sourceDetail, formatReportText } from './CaseReport'

afterEach(cleanup)

const caseId = '00000000-0000-4000-8000-000000000001'
const evidenceId = '00000000-0000-4000-8000-000000000002'
const providerId = '00000000-0000-4000-8000-000000000003'
const evidence = { id: evidenceId, kind: 'scene_photo', mime_type: 'image/png', role: 'vue_ensemble', original_filename: null, client_sha256: 'a'.repeat(64), checksum_status: 'verified' } as Evidence
const provider = { id: providerId, provider: 'insurance_lookup', mode: 'mock', status: 'unavailable', query: {}, data: {}, reason: null } as ProviderResult

function line(id: string, kind: ReportLine['claim_kind'], text: string, sourceRefs: ReportLine['source_refs']): ReportLine {
  return { id, text, claim_kind: kind, source_refs: sourceRefs, uncertainty: null,
    as_of_revision: 2, stale: false, mode: kind === 'provider_result' ? 'mock' : null,
    signed_by: null, signed_at: null, previous_text: null }
}

const caseView = {
  id: caseId, state_version: 2, content_revision: 2, scenario_id: 'g1',
  intake: { location: 'Paris', narrative: 'Récit déclaré' }, evidence: [evidence],
  provider_results: [provider], latest_analysis: null,
  report: { analysis_current: false, needs_reanalysis: true, lines: [
    line('intake.location', 'declaration', 'Lieu déclaré : Paris', [{ kind: 'intake', id: caseId, locator: 'location' }]),
    line('evidence.1', 'observation', 'Pièce reçue : vue ensemble.', [{ kind: 'evidence', id: evidenceId, locator: 'kind' }]),
    line('provider.1', 'provider_result', 'Assurance indisponible.', [{ kind: 'provider_result', id: providerId, locator: 'status' }]),
    line('unknown.1', 'hypothesis', 'Plaque BMW inconnue.', []),
  ] },
} as CaseView

it('renders declaration, evidence receipt, mock provider result and unknown without merging their sources', () => {
  const open = vi.fn()
  render(<CaseReport caseView={caseView} accessToken="token" onUpdated={() => {}} onOpenEvidence={open} />)
  expect(screen.getByText('Déclaration')).toBeTruthy()
  expect(screen.getByText('Constat de pièce')).toBeTruthy()
  expect(screen.getByText('Résultat fournisseur · simulation')).toBeTruthy()
  expect(screen.getByText('À vérifier')).toBeTruthy()
  expect(screen.getByText(/analyse précédente est obsolète/i)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Ouvrir la pièce' }))
  expect(open).toHaveBeenCalledWith(evidence)
  expect(screen.getByText(/SHA-256 a+/)).toBeTruthy()
})

it('resolves only locators on the current case', () => {
  expect(sourceDetail(caseView, { kind: 'intake', id: caseId, locator: 'location' })).toContain('Paris')
  expect(sourceDetail(caseView, { kind: 'provider_result', id: providerId, locator: 'status' })).toContain('unavailable')
  expect(sourceDetail(caseView, { kind: 'evidence', id: 'foreign', locator: 'kind' })).toBeNull()
})

 it('formats timestamp offsets as local HH:MM without changing ordinary text', () => {
  const timestamp = '2025-06-14T15:32:00+00:00'
  const localTime = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(timestamp))
  expect(formatReportText(`Heure de l'incident : ${timestamp}`)).toBe(`Heure de l'incident : ${localTime}`)
  expect(formatReportText('Lieu déclaré : Paris')).toBe('Lieu déclaré : Paris')
})

it('keeps editing available without technical source links and redundant notes', () => {
  const view = { ...caseView, report: { ...caseView.report, lines: [
    { ...caseView.report.lines[0], uncertainty: 'Déclaration non corroborée par les pièces.' },
    { ...caseView.report.lines[2], uncertainty: "Résultat simulé ; distinct d'une observation de pièce." },
  ] } }
  render(<CaseReport caseView={view} accessToken="token" onUpdated={() => {}} onOpenEvidence={() => {}} />)
  expect(screen.queryByText(/Voir la source/)).toBeNull()
  expect(screen.queryByText(/non corroborée|distinct d'une observation/)).toBeNull()
  expect(screen.getAllByRole('button', { name: 'Modifier' })).toHaveLength(2)
  fireEvent.click(screen.getAllByRole('button', { name: 'Modifier' })[0])
  expect(screen.getByRole('textbox', { name: 'Texte' })).toBeTruthy()
})
