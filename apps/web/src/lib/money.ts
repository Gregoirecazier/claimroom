export function formatMoney(minor: number, currency = 'EUR'): string {
  return new Intl.NumberFormat('fr-FR', {
    style: 'currency', currency,
    minimumFractionDigits: minor % 100 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(minor / 100)
}

export function parseEuroCents(value: string): number | null {
  const normalized = value.trim().replace(',', '.')
  if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) return null
  const [euros, cents = ''] = normalized.split('.')
  const amount = Number(euros) * 100 + Number(cents.padEnd(2, '0'))
  return Number.isSafeInteger(amount) ? amount : null
}
