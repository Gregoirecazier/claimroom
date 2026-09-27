import { formatMoney as money } from '../lib/money'
import type { AccidentJourneyView } from '../lib/api'
import { repairBreakdowns, repairTotal } from '../lib/repairBreakdown'
import './repair-cost-breakdown.css'

const range = (minimum: number, maximum: number, currency: string) => minimum === maximum
  ? money(minimum, currency) : `${money(minimum, currency)} – ${money(maximum, currency)}`

export function RepairCostBreakdown({ assessment }: {
  assessment: NonNullable<AccidentJourneyView['review']>['assessment']
}) {
  const vehicles = repairBreakdowns(assessment)
  if (!vehicles.length) return <p className="aj-empty">Estimation à compléter.</p>
  return <div className="aj-repair-breakdowns">{vehicles.map(({ vehicle, insured, parts, estimate }) => {
    const items = estimate?.line_items || []
    const total = estimate ? repairTotal(estimate) : null
    return <div className="aj-repair-vehicle" key={vehicle}>
      <p className="aj-subtitle">{vehicle}</p>
      {!insured && <p className="aj-meta aj-repair-role">{assessment.insured_vehicle ? 'Véhicule observé' : 'Véhicule assuré à confirmer'}</p>}
      {items.length > 0 && estimate && total ? <table className="aj-costs aj-repair-addition">
        <caption className="aj-sr-only">{insured ? 'Postes de réparation du véhicule assuré' : `Postes de réparation : ${vehicle}`}</caption>
        <thead><tr><th scope="col">Partie endommagée</th><th scope="col">Estimation</th></tr></thead>
        <tbody>{items.map((item, index) => <tr key={`${item.label}-${index}`}>
          <th scope="row"><span aria-hidden="true" className="aj-addition-sign">{index > 0 ? '+' : ''}</span>{item.label}</th>
          <td>{range(item.minimum_minor, item.maximum_minor, estimate.currency)}</td>
        </tr>)}</tbody>
        <tfoot><tr><th scope="row"><span aria-hidden="true" className="aj-addition-sign">=</span>Total estimé</th>
          <td>{range(total.minimum_minor, total.maximum_minor, estimate.currency)}</td></tr></tfoot>
      </table> : <>
        {parts.length > 0 && <ul className="aj-repair-pending">{parts.map(part => <li key={part}><span>{part}</span><span>À chiffrer</span></li>)}</ul>}
        {estimate && total ? <div className="aj-global-estimate"><span className="aj-label">Estimation globale</span>
          <strong>{range(total.minimum_minor, total.maximum_minor, estimate.currency)}</strong>
          <span className="aj-meta">Détail par poste non disponible.</span>
        </div> : <p className="aj-empty">Estimation à compléter.</p>}
      </>}
      {estimate && <details className="aj-details"><summary>Hypothèses du chiffrage</summary><p>{estimate.assumptions}</p></details>}
    </div>
  })}</div>
}
