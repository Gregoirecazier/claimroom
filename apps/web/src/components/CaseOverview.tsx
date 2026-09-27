import type { CaseView, Evidence } from '../lib/api'
import { displayLabel } from '../lib/presentation'
import { formatCaseAmount, formatCaseDate, nextCaseAction } from '../lib/caseJourney'

const paths = {
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  folder: 'M3 7V5h6l2 2h10v13H3z',
  clock: 'M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  file: 'M14 2H5v20h14V7zM14 2v6h5M8 13h8M8 17h5',
  note: 'M21 11v10H3V3h10M9 15l1-5L19 1l4 4-9 9z',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  download: 'M12 3v12M7 10l5 5 5-5M4 16v5h16v-5',
  building: 'M3 21h18M5 21V5l10-2v18M15 10h4v11M9 8h2M9 12h2M9 16h2',
  mic: 'M8 5a4 4 0 0 1 8 0v7a4 4 0 0 1-8 0zM5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8',
  image: 'M3 3h18v18H3zM3 17l6-6 4 4 3-3 5 5M15 7h.01',
  check: 'M5 12l4 4L19 6',
}
export function CaseIcon({ name }: { name: keyof typeof paths }) {
  return <svg className="cr-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>
}

export function CaseOverview({ view, onOpenEvidence }: { view: CaseView; onOpenEvidence: (evidence: Evidence) => void }) {
  const next = nextCaseAction(view)
  const recipient = view.current_draft?.recipient
  const recipientName = String(recipient?.name || recipient?.correspondent_name || recipient?.organization || 'À identifier')
  const name = view.intake.insured_name || 'Assuré à identifier'
  const initials = name.split(' ').slice(0, 2).map(part => part[0]).join('')
  const latestEvents = [...view.timeline].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at)).slice(-3)
  const photos = view.evidence.filter(item => item.mime_type.startsWith('image/'))
  const videos = view.evidence.filter(item => item.mime_type.startsWith('video/'))
  const quote = view.evidence.find(item => item.id === view.quote?.evidence_id)
  const note = view.current_draft?.transmission_comment

  return <div className="cr-overview" id="overview">
    <section className={`cr-next-action${next.urgent ? ' cr-next-urgent' : ''}`} aria-labelledby="next-action-title">
      {next.urgent && <p className="cr-kicker">REPRISE HUMAINE PRIORITAIRE</p>}
      <h2 id="next-action-title">{next.title}</h2>
      <div className="cr-next-bottom"><a className="ir-button cr-light-button" href={`#${next.target}`}>{next.label}<CaseIcon name="arrow" /></a></div>
    </section>
    <div className="cr-overview-grid">
      <section className="cr-card cr-essentials" aria-labelledby="essentials-title"><h2 id="essentials-title">L’essentiel</h2><dl>
        <div><dt>Date de l’accident</dt><dd>{formatCaseDate(view.intake.incident_at)}</dd></div>
        <div><dt>Lieu</dt><dd>{view.intake.location || 'À confirmer'}</dd></div>
        <div><dt>Véhicule assuré</dt><dd>{view.intake.insured_vehicle || 'À confirmer'}<small>{view.intake.insured_plate || 'Plaque à confirmer'}</small></dd></div>
        <div><dt>Montant estimé</dt><dd className="cr-amount">{formatCaseAmount(view.estimate?.total_minor)}{view.estimate && <small>TTC · <a href="#estimate">Voir le devis</a></small>}</dd></div>
        <div><dt>Responsabilité</dt><dd><a href="#sourced-report">{view.latest_analysis ? 'Consulter l’analyse' : 'À examiner'}<CaseIcon name="arrow" /></a></dd></div>
      </dl></section>
      <section className="cr-card" aria-labelledby="people-title"><h2 id="people-title">Intervenants</h2><ul className="cr-people">
        <li><span className="cr-avatar">{initials}</span><div><strong>{name}</strong><small>Assuré</small></div></li>
        <li><span className="cr-avatar"><CaseIcon name="building" /></span><div><strong>{recipientName}</strong><small>Destinataire proposé</small></div></li>
        <li><span className="cr-avatar cr-avatar-muted">GE</span><div><strong>Espace gestionnaire</strong></div></li>
      </ul></section>
      <section className="cr-card" aria-labelledby="documents-title"><div className="cr-card-heading"><h2 id="documents-title">Pièces</h2><span className="cr-count">{view.evidence.length}</span></div><ul className="cr-document-list">
        <li>{quote ? <button onClick={() => onOpenEvidence(quote)}><CaseIcon name="file" /><span>Devis de réparation</span><CaseIcon name="arrow" /></button> : <a href="#estimate"><CaseIcon name="file" /><span>Devis à associer</span><CaseIcon name="arrow" /></a>}</li>
        <li><a href="#evidence"><CaseIcon name="image" /><span>{photos.length ? `Photos du sinistre (${photos.length})` : 'Photos à ajouter'}</span><CaseIcon name="arrow" /></a></li>
        <li>{videos[0] ? <button onClick={() => onOpenEvidence(videos[0])}><CaseIcon name="file" /><span>{videos.length > 1 ? `Vidéos de l’accident (${videos.length})` : 'Vidéo de l’accident'}</span><CaseIcon name="arrow" /></button> : <a href="#evidence"><CaseIcon name="file" /><span>Vidéo à ajouter</span><CaseIcon name="arrow" /></a>}</li>
      </ul><a className="cr-subtle-link" href="#evidence">Toutes les pièces <CaseIcon name="arrow" /></a></section>
    </div>
    <section className="cr-card" id="chronologie" aria-labelledby="chronology-title"><div className="cr-card-heading"><h2 id="chronology-title">Chronologie</h2><span className="cr-muted">{view.timeline.length} événements</span></div>
      {latestEvents.length ? <ol className="cr-timeline">{latestEvents.map(event => <li key={event.id}><span className="cr-event-dot" /><div><strong>{displayLabel(event.event_type.replace('case.', ''))}</strong><small>{formatCaseDate(event.occurred_at)}</small></div></li>)}</ol> : <p className="cr-muted">Aucun événement enregistré.</p>}
      {view.timeline.length > 3 && <details className="cr-history"><summary>Tout l’historique</summary><ol>{[...view.timeline].reverse().map(event => <li key={event.id}><strong>{displayLabel(event.event_type.replace('case.', ''))}</strong><time>{formatCaseDate(event.occurred_at)}</time></li>)}</ol></details>}
    </section>
    <section className="cr-card" id="notes" aria-labelledby="notes-title"><div className="cr-card-heading"><h2 id="notes-title">Note de transmission</h2>{view.current_draft && !['registered', 'sent'].includes(view.status) && <a className="cr-subtle-link" href="#transmission-comment"><CaseIcon name="note" />{note ? 'Modifier' : 'Ajouter une note'}</a>}</div>
      <p className={note ? 'cr-note' : 'cr-muted'}>{note || (view.current_draft ? 'Aucune note pour le moment.' : 'Disponible après préparation du recours.')}</p>
    </section>
  </div>
}
