import { formatMoney as euro } from '../lib/money'
import { useEffect, useState } from 'react'
import { attachQuote, removeQuote, upsertEstimate } from '../lib/api'
import type { CaseView, EstimateItem, Evidence } from '../lib/api'
import { parseEuroCents } from '../lib/money'

type Props = {
  caseView: CaseView
  accessToken: string
  onUpdated: (caseView: CaseView) => void
  onOpenEvidence: (evidence: Evidence) => void
}

type ItemForm = { id: string; label: string; amount: string }

const formFromItem = (item: EstimateItem): ItemForm => ({ id: item.id, label: item.label, amount: (item.amount_minor / 100).toFixed(2) })

export function EstimatePanel({ caseView, accessToken, onUpdated, onOpenEvidence }: Props) {
  const [items, setItems] = useState<ItemForm[]>(caseView.estimate?.line_items.map(formFromItem) || [])
  const [editing, setEditing] = useState(false)
  const [quoteEvidenceId, setQuoteEvidenceId] = useState(caseView.quote?.evidence_id || '')
  const [quoteAmount, setQuoteAmount] = useState(caseView.quote ? (caseView.quote.total_ttc_minor / 100).toFixed(2) : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const pdfs = caseView.evidence.filter(item => item.mime_type === 'application/pdf' && item.kind === 'document')
  const readOnly = caseView.status === 'registered' || caseView.status === 'sent'

  useEffect(() => {
    setItems(caseView.estimate?.line_items.map(formFromItem) || [])
    setEditing(false)
  }, [caseView.estimate?.id])
  useEffect(() => {
    setQuoteEvidenceId(caseView.quote?.evidence_id || '')
    setQuoteAmount(caseView.quote ? (caseView.quote.total_ttc_minor / 100).toFixed(2) : '')
  }, [caseView.quote?.id])

  function updateItem(id: string, patch: Partial<ItemForm>) {
    setItems(current => current.map(item => item.id === id ? { ...item, ...patch } : item))
  }

  async function saveEstimate() {
    const parsed = items.map(item => ({ id: item.id, label: item.label.trim(), amount_minor: parseEuroCents(item.amount) }))
    if (!parsed.length || parsed.some(item => !item.label || item.amount_minor === null)) {
      setError('Chaque poste doit avoir un libellé et un montant en euros à deux décimales maximum.'); return
    }
    const lineItems = parsed as EstimateItem[]
    const total = lineItems.reduce((sum, item) => sum + item.amount_minor, 0)
    if (!Number.isSafeInteger(total) || total <= 0) { setError('Le total TTC doit être strictement positif.'); return }
    setBusy(true); setError(''); setNotice('')
    try {
      onUpdated(await upsertEstimate(accessToken, caseView.id, caseView.state_version, lineItems, total))
      setEditing(false)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Estimation indisponible.') }
    finally { setBusy(false) }
  }

  async function saveQuote() {
    const amount = parseEuroCents(quoteAmount)
    if (!quoteEvidenceId || amount === null || amount <= 0) {
      setError('Sélectionnez un PDF finalisé et saisissez son total TTC en euros.'); return
    }
    setBusy(true); setError(''); setNotice('')
    try {
      onUpdated(await attachQuote(accessToken, caseView.id, caseView.state_version, quoteEvidenceId, amount))
      setNotice('Devis associé à l’estimation actuelle.')
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Association du devis impossible.') }
    finally { setBusy(false) }
  }

  async function clearQuote() {
    setBusy(true); setError(''); setNotice('')
    try { onUpdated(await removeQuote(accessToken, caseView.id, caseView.state_version)) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Retrait du devis impossible.') }
    finally { setBusy(false) }
  }

  const quoteEvidence = pdfs.find(item => item.id === caseView.quote?.evidence_id)
  return <section className="ir-card ir-estimate" id="estimate" aria-labelledby="estimate-title">
    <div className="ir-section-heading"><span className="ir-heading-icon">€</span><div><h2 id="estimate-title">Coût de réparation</h2></div></div>
    {caseView.estimate ? <><p><strong>{euro(caseView.estimate.total_minor)} TTC</strong> · {caseView.estimate.estimate_source === 'demo_fixture' ? 'hypothèse de démonstration, non chiffrage garage' : 'saisie gestionnaire'}</p>
      <ul className="ir-estimate-items">{caseView.estimate.line_items.map(item => <li key={item.id}><span>{item.label}</span><strong>{euro(item.amount_minor)}</strong></li>)}</ul>
    </> : <p>Aucune estimation enregistrée.</p>}
    {!readOnly && <button className="ir-button ir-secondary" onClick={() => setEditing(value => !value)}>{editing ? 'Annuler la modification' : 'Modifier les postes'}</button>}
    {editing && <div className="ir-estimate-edit">{items.map(item => <div key={item.id} className="ir-estimate-item-edit"><label className="ir-field">Poste<input value={item.label} onChange={event => updateItem(item.id, { label: event.target.value })} /></label><label className="ir-field">Montant TTC (€)<input inputMode="decimal" value={item.amount} onChange={event => updateItem(item.id, { amount: event.target.value })} /></label><button className="ir-text-button" onClick={() => setItems(current => current.filter(row => row.id !== item.id))}>Retirer le poste</button></div>)}<button className="ir-button ir-secondary" onClick={() => setItems(current => [...current, { id: crypto.randomUUID(), label: '', amount: '0.00' }])}>Ajouter un poste</button><button className="ir-button ir-primary" disabled={busy} onClick={() => void saveEstimate()}>Enregistrer l’estimation</button></div>}
    <div className="ir-quote-status" role="status">
      {caseView.quote_status === 'matched' && <p>Devis aligné avec l’estimation actuelle.</p>}
      {caseView.quote_status === 'mismatch' && <p className="ir-error">Écart bloquant : estimation {euro(caseView.estimate!.total_minor)} ; devis {euro(caseView.quote!.total_ttc_minor)}.</p>}
      {caseView.quote_status === 'outdated' && <p className="ir-error">L’estimation a changé depuis l’association du devis. Vérifiez le PDF et son montant, puis associez-le de nouveau.</p>}
      {caseView.quote_status === 'no_quote' && <p>Aucun devis associé à l’estimation.</p>}
      {caseView.quote_status === 'no_estimate' && <p>Ajoutez une estimation pour comparer un devis.</p>}
    </div>
    {caseView.quote && <div className="ir-estimate-note"><strong>{caseView.quote.filename}</strong><p>Total TTC saisi par le gestionnaire : {euro(caseView.quote.total_ttc_minor)}. Ce montant n’a pas été lu automatiquement dans le PDF.</p>{quoteEvidence && <button className="ir-text-button" onClick={() => onOpenEvidence(quoteEvidence)}>Ouvrir le devis</button>}{!readOnly && <button className="ir-text-button" disabled={busy} onClick={() => void clearQuote()}>Retirer l’association</button>}</div>}
    {!readOnly && <div className="ir-quote-form"><label className="ir-field">Document du devis<select value={quoteEvidenceId} onChange={event => setQuoteEvidenceId(event.target.value)}><option value="">Choisir une pièce du dossier</option>{pdfs.map(item => <option key={item.id} value={item.id}>{item.original_filename || `PDF ${item.id.slice(0, 8)}`}</option>)}</select></label><label className="ir-field">Total TTC du PDF (€) · saisi manuellement<input inputMode="decimal" value={quoteAmount} onChange={event => setQuoteAmount(event.target.value)} /></label><button className="ir-button ir-primary" disabled={busy || !pdfs.length} onClick={() => void saveQuote()}>{caseView.quote ? 'Réassocier le devis' : 'Associer le devis'}</button>{!pdfs.length && <small><a href="#evidence">Ajoutez un PDF aux pièces du dossier</a> pour l’associer ici.</small>}</div>}
    {notice && <p role="status">{notice}</p>}
    {error && <p className="ir-error" role="alert">{error}</p>}
  </section>
}
