// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AccidentJourneyPanel } from './AccidentJourneyPanel'
import { approveAccident, editAccidentEstimate, getAccidentJourney, getCase, getMediaWorkflow, sendAccidentNotifications } from '../lib/api'
import { accidentJourney } from '../lib/accidentJourney.fixture'
import { reviewableCase } from '../lib/caseJourney.fixture'
vi.mock('../lib/api', () => ({ getAccidentJourney: vi.fn(), getCase: vi.fn(), getMediaWorkflow: vi.fn(), approveAccident: vi.fn(), editAccidentEstimate: vi.fn(), sendAccidentNotifications: vi.fn(),
  advanceMediaWorkflow: vi.fn(), retryMediaWorkflow: vi.fn(), accidentVideo: vi.fn() }))
beforeEach(() => {
  vi.mocked(getAccidentJourney).mockResolvedValue(accidentJourney())
  vi.mocked(getMediaWorkflow).mockResolvedValue({ workflow: { status: 'ready' } } as never)
  vi.mocked(getCase).mockResolvedValue(reviewableCase())
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const props = () => ({ caseView: reviewableCase(), accessToken: 'test', onUpdated: vi.fn(), onOpenEvidence: vi.fn() })
it('keeps an amended amount while navigating and sends only after a second click', async () => {
  const p = props(); const { rerender } = render(<AccidentJourneyPanel {...p} />)
  const amount = await screen.findByRole('spinbutton', { name: 'Montant retenu (€)' })
  expect((amount as HTMLInputElement).value).toBe('3000')
  expect(screen.getByRole('region', { name: 'Coût des réparations' }).contains(amount)).toBe(true)
  expect(approveAccident).not.toHaveBeenCalled()
  fireEvent.change(amount, { target: { value: '3500' } })
  expect((screen.getByRole('button', { name: 'Valider le montant' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(screen.getByRole('textbox', { name: 'Motif de modification' }), { target: { value: 'Remplacement du feu ajouté.' } })
  rerender(<AccidentJourneyPanel {...p} hidden />)
  rerender(<AccidentJourneyPanel {...p} />)
  expect((screen.getByRole('spinbutton') as HTMLInputElement).value).toBe('3500')
  const result = accidentJourney(); result.review!.status = 'approved'; result.review!.approved_amount_minor = 350000
  result.notification_preview = [{ channel: 'sms', recipient: '+33••••3415', mode: 'simulated', status: 'ready', reason: null },
    { channel: 'email', recipient: 'test@example.com', mode: 'simulated', status: 'ready', reason: null }]
  vi.mocked(approveAccident).mockResolvedValue(result)
  fireEvent.click(screen.getByRole('button', { name: 'Valider le montant' }))
  await screen.findByText('Montant validé')
  expect(approveAccident).toHaveBeenCalledExactlyOnceWith('test', p.caseView.id, { review_id: 'review-id', expected_content_revision: 2, amount_minor: 350000, amendment_reason: 'Remplacement du feu ajouté.' })
  expect(sendAccidentNotifications).not.toHaveBeenCalled()
  const sent = structuredClone(result)
  sent.notifications = ['sms','email'].map(channel => ({ id: channel, channel: channel as 'sms' | 'email', recipient: 'test', subject: 'Test', body: '3500 EUR validés', mode: 'simulated', status: 'simulated', error_code: null }))
  vi.mocked(sendAccidentNotifications).mockResolvedValue(sent)
  fireEvent.click(screen.getByRole('button', { name: 'Simuler l’envoi du SMS et de l’email' }))
  await screen.findByText('3500 EUR validés')
  expect(sendAccidentNotifications).toHaveBeenCalledExactlyOnceWith('test', p.caseView.id, { review_id: 'review-id', expected_content_revision: 2, confirm_send: true })
  expect(screen.getByText('3500 EUR validés')).toBeTruthy()
  expect(screen.queryByRole('spinbutton')).toBeNull()
})
it('shows the precise missing information and prevents approval of an ambiguous match', async () => {
  const view = accidentJourney(); view.review!.status = 'needs_information'; view.review!.blockers = ['Plaque du tiers illisible.']
  vi.mocked(getAccidentJourney).mockResolvedValue(view)
  render(<AccidentJourneyPanel {...props()} />)
  expect(await screen.findByText('Plaque du tiers illisible.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Valider le montant' })).toBeNull()
})
it('edits the repair range through the pencil and keeps item totals in sync', async () => {
  render(<AccidentJourneyPanel {...props()} />)
  await screen.findByRole('table', { name: 'Postes de réparation du véhicule assuré' })
  expect(screen.queryByText('Indicatif')).toBeNull()
  expect(screen.queryByText('Milieu de la fourchette')).toBeNull()
  expect(screen.queryByText('Montant proposé')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Modifier le chiffrage' }))
  const minimum = screen.getAllByRole('spinbutton', { name: 'Minimum (€)' })[0]
  fireEvent.change(minimum, { target: { value: '1200' } })
  expect(screen.getByText(/Nouvelle fourchette totale/).textContent?.replace(/\s/g, '')).toContain('2100€–4000€')
  const changed = accidentJourney()
  changed.review!.assessment.repair_estimate!.line_items![0].minimum_minor = 120000
  changed.review!.assessment.repair_estimate!.minimum_minor = 210000
  vi.mocked(editAccidentEstimate).mockResolvedValue(changed)
  fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le chiffrage' }))
  await screen.findByRole('button', { name: 'Modifier le chiffrage' })
  expect(editAccidentEstimate).toHaveBeenCalledExactlyOnceWith('test', props().caseView.id, {
    review_id: 'review-id', expected_content_revision: 2, minimum_minor: 210000, maximum_minor: 400000,
    line_items: [{ minimum_minor: 120000, maximum_minor: 220000 }, { minimum_minor: 50000, maximum_minor: 100000 },
      { minimum_minor: 40000, maximum_minor: 80000 }],
  })
})
it('keeps the validated amount in the analysis without a notifications section', async () => {
  const view = accidentJourney(); view.review!.status = 'approved'; view.review!.approved_amount_minor = 300000
  view.notifications = [{ id: 'sms', channel: 'sms', recipient: '+33••••3415', subject: '', body: 'Validation', mode: 'live', status: 'unknown', error_code: 'sms_delivery_unknown' }]
  vi.mocked(getAccidentJourney).mockResolvedValue(view)
  render(<AccidentJourneyPanel {...props()} />)
  const validated = await screen.findByText('Montant validé')
  expect(screen.getByRole('region', { name: 'Coût des réparations' }).contains(validated)).toBe(true)
  expect(screen.queryByRole('heading', { name: 'Notifications' })).toBeNull()
  expect(screen.getByText(/\+33••••3415/, { exact: false })).toBeTruthy()
  expect(screen.getByText('Validation')).toBeTruthy()
  expect(screen.queryByText('Accepté par le service d’envoi')).toBeNull()
})

it('shows repair line items with a reconciled total and keeps supporting details collapsed', async () => {
  render(<AccidentJourneyPanel {...props()} />)
  const table = await screen.findByRole('table', { name: 'Postes de réparation du véhicule assuré' })
  expect(table.querySelectorAll('tbody tr')).toHaveLength(3)
  expect(table.textContent).toContain('Remplacement du pare-chocs')
  expect(table.querySelector('tfoot')?.textContent?.replace(/\s/g, '')).toBe('=Totalestimé2000€–4000€')
  expect(screen.getByText('Hypothèses du chiffrage').closest('details')?.open).toBe(false)
  expect(screen.queryByText(/La validation déclenche automatiquement/)).toBeNull()
  expect(screen.queryByText(/GPT Astra compare/)).toBeNull()
})

it('keeps older global estimates readable without fabricating repair amounts', async () => {
  const view = accidentJourney(); delete view.review!.assessment.repair_estimate!.line_items
  vi.mocked(getAccidentJourney).mockResolvedValue(view)
  render(<AccidentJourneyPanel {...props()} />)
  await screen.findByText('Estimation globale')
  expect(screen.queryByRole('table')).toBeNull()
  expect(screen.getByText('Détail par poste non disponible.')).toBeTruthy()
  expect((screen.getByRole('spinbutton') as HTMLInputElement).value).toBe('3000')
})

it('keeps precise missing information visible when an estimate cannot be established', async () => {
  const view = accidentJourney(); view.review!.status = 'needs_information'
  view.review!.assessment.repair_estimate = null
  view.review!.assessment.missing_information = ['Confirmer le côté endommagé.']
  vi.mocked(getAccidentJourney).mockResolvedValue(view)
  render(<AccidentJourneyPanel {...props()} />)
  await screen.findByText('Estimation à compléter.')
  expect(screen.getByText('Confirmer le côté endommagé.')).toBeTruthy()
  expect(screen.queryByRole('spinbutton')).toBeNull()
})

it('turns legacy prose into short fact lists without video comparisons or repeated reasoning', async () => {
  const view = accidentJourney()
  view.review!.assessment.media.summary = 'Dans la vidéo synthétique retenue, une BMW recule vers une Peugeot immobile. Un contact apparaît à l’arrière gauche. La BMW est probablement responsable. Le champ repair_estimate reste null.'
  vi.mocked(getAccidentJourney).mockResolvedValue(view)
  render(<AccidentJourneyPanel {...props()} />)
  const accident = await screen.findByRole('region', { name: 'L’accident' })
  expect(within(accident).getAllByRole('listitem').map(item => item.textContent)).toEqual([
    'Une BMW recule vers une Peugeot immobile.', 'Un contact apparaît à l’arrière gauche.',
  ])
  expect(screen.queryByText(/Correspondance des vidéos|repair_estimate|La BMW est probablement responsable/)).toBeNull()
  expect(screen.queryByText(view.review!.assessment.media.liability.reasoning)).toBeNull()
  expect(screen.queryByRole('button', { name: 'Lire la suite' })).toBeNull()
  expect(within(accident).getByRole('button', { name: 'Voir la vidéo source' })).toBeTruthy()
})

it('keeps only the two involved cars and their damage when insurance roles remain unknown', async () => {
  const view = accidentJourney()
  const assessment = view.review!.assessment
  assessment.insured_vehicle = null
  assessment.media.plates[0].role = 'unknown'
  assessment.media.plates.push({ ...assessment.media.plates[0], vehicle: 'BMW', plate: 'AB12 CDE' })
  assessment.media.damages.push({ ...assessment.media.damages[0], vehicle: 'BMW', accident_link: 'uncertain', description: 'Dégâts peu visibles sur la BMW.' })
  for (let i = 1; i <= 4; i++) {
    const vehicle = `Véhicule d’arrière-plan ${i}`
    assessment.media.plates.unshift({ ...assessment.media.plates[0], vehicle, plate: null })
    assessment.media.damages.unshift({ ...assessment.media.damages[0], vehicle, accident_link: 'uncertain', description: 'Aucun lien établi avec le choc.' })
  }
  vi.mocked(getAccidentJourney).mockResolvedValue(view)
  render(<AccidentJourneyPanel {...props()} />)
  const vehicles = (await screen.findByText('Véhicules impliqués')).closest('details')!
  const damages = screen.getByText('Dommages observés').closest('details')!
  fireEvent.click(within(vehicles).getByText('Véhicules impliqués'))
  fireEvent.click(within(damages).getByText('Dommages observés'))
  expect(within(vehicles).getAllByRole('article')).toHaveLength(2)
  expect(within(vehicles).getAllByText('Rôle à confirmer')).toHaveLength(2)
  expect(within(damages).getAllByRole('article')).toHaveLength(2)
  expect(within(damages).getByText('Dégâts peu visibles sur la BMW.').tagName).toBe('LI')
  expect(screen.queryByText(/Véhicule d’arrière-plan/)).toBeNull()
})

it('uses the bounded insurer summary for new assessments and keeps blocking uncertainty visible', async () => {
  const view = accidentJourney()
  view.review!.assessment.involved_vehicles = ['Peugeot']
  view.review!.assessment.at_fault_vehicle = null
  view.review!.assessment.key_facts = ['Contact arrière gauche ; manœuvre initiale hors champ.']
  view.review!.status = 'needs_information'
  view.review!.blockers = ['Confirmer si le véhicule assuré est la Peugeot ou la Renault.']
  vi.mocked(getAccidentJourney).mockResolvedValue(view)
  render(<AccidentJourneyPanel {...props()} />)
  expect((await screen.findByText('Contact arrière gauche ; manœuvre initiale hors champ.')).tagName).toBe('LI')
  expect(screen.getByText('Confirmer si le véhicule assuré est la Peugeot ou la Renault.')).toBeTruthy()
  expect(screen.queryByText(view.review!.assessment.media.summary)).toBeNull()
  expect(screen.queryByRole('button', { name: 'Valider le montant' })).toBeNull()
})
