const euros = minor => new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR' }).format(minor / 100)
const distance = metres => metres < 1000 ? `${metres} m` : `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 }).format(metres / 1000)} km`

export function claimSms(c, assessment, amountMinor) {
  const estimate = assessment?.repair_estimate
  const proposed = estimate ? Math.floor((estimate.minimum_minor + estimate.maximum_minor) / 2) : null
  const subject = c.intake.insured_vehicle ? ` pour votre ${c.intake.insured_vehicle}` : ''
  const introduction = c.intake.insured_name ? `Bonjour ${c.intake.insured_name}, ` : 'Bonjour, '
  const amountLabel = amountMinor === proposed ? 'montant proposé' : 'montant retenu'
  const parts = [`${introduction}pour le dossier ${c.intake.insured_reference}, le ${amountLabel} des réparations${subject} est de ${euros(amountMinor)}.`]
  if (estimate?.line_items?.length) {
    const items = estimate.line_items.map(item => `${item.label.toLowerCase()} (${euros(Math.floor((item.minimum_minor + item.maximum_minor) / 2))})`)
    parts.push(`Ce chiffrage comprend ${items.join(', ')}.`)
  }
  parts.push('Le devis du réparateur, après démontage si nécessaire, permettra de confirmer ces montants.')
  const garages = (c.partner_garages || []).filter(garage => garage.network_status === 'approved')
  if (garages.length) parts.push(`Garages agréés près du lieu déclaré : ${garages.map(garage => `${garage.name} (${distance(garage.distance_m)})`).join(', ')}.`)
  return parts.join(' ')
}
