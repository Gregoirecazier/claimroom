"""Automatic assessment with one handler decision and durable, deduplicated notifications."""
from __future__ import annotations

import os
import re
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import Field

from claim_api.analysis import AnalysisError
from claim_api.video_reuse import PostgresVideoRepository
from claim_api.astra_analysis import AstraAccidentAnalyzer, AccidentAssessment, RepairEstimate
from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.correspondence import CorrespondenceError, ResendMailer
from claim_api.mock_insurance import PATTERNS, insurance_lookup, normalize_plate
from claim_api.models import ContractModel
from claim_api.reference_data import ReferenceData
from claim_api.sms_recipient import mask_e164, validate_e164
from claim_api.sms_service import SmsError
from claim_api.sms_link import PROVIDER as SMS_LINK_PROVIDER


def _euros(minor: int) -> str:
    amount = minor / 100
    digits = f'{amount:,.0f}' if minor % 100 == 0 else f'{amount:,.2f}'
    return f'{digits.replace(",", " ")} €'


def claim_approval_sms(case, assessment: AccidentAssessment, amount_minor: int) -> str:
    """Describe the approved repair figure using only facts stored in this case."""
    estimate = assessment.repair_estimate
    intake = case.get('intake_json') or {}
    proposed = (estimate.minimum_minor + estimate.maximum_minor) // 2 if estimate else None
    label = 'montant proposé' if amount_minor == proposed else 'montant retenu'
    vehicle = f' pour votre {assessment.insured_vehicle}' if assessment.insured_vehicle else ''
    reference = f" pour le dossier {intake['insured_reference']}" if intake.get('insured_reference') else ''
    greeting = f"Bonjour {intake['insured_name']}, " if intake.get('insured_name') else 'Bonjour, '
    parts = [f'{greeting}le {label} des réparations{vehicle}{reference} est de {_euros(amount_minor)}.']
    if estimate and estimate.line_items:
        breakdown = ', '.join(
            f'{item.label.lower()} ({_euros((item.minimum_minor + item.maximum_minor) // 2)})'
            for item in estimate.line_items
        )
        parts.append(f'Ce chiffrage comprend {breakdown}.')
    parts.append('Le devis du réparateur, après démontage si nécessaire, permettra de confirmer ces montants.')
    return ' '.join(parts)


def photo_video_match(case, review, photo_id):
    """Expose only a plate cited in this photo and the selected catalogue video."""
    photo = next((item for item in case.evidence if item.id == photo_id and
                  item.mime_type.startswith('image/')), None)
    if photo is None:
        raise ValueError('Photo absente du dossier.')
    if not review:
        return {'status': 'pending', 'photo_id': str(photo_id), 'plate': None, 'video_id': None}
    assessment = AccidentAssessment.model_validate(review['assessment'])
    video_id = assessment.selected_video_id
    if video_id is None:
        return {'status': 'no_match', 'photo_id': str(photo_id), 'plate': None, 'video_id': None}
    plates = {normalize_plate(finding.plate) for finding in assessment.media.plates
              if finding.legibility == 'readable' and finding.plate
              and any(re.fullmatch(pattern, normalize_plate(finding.plate)) for pattern in PATTERNS.values())
              and any(c.evidence_id == photo_id and c.timestamp_seconds is None
                      for c in finding.citations)
              and any(c.evidence_id == video_id and c.timestamp_seconds is not None
                      for c in finding.citations)}
    if len(plates) != 1:
        return {'status': 'ambiguous' if plates else 'no_match',
                'photo_id': str(photo_id), 'plate': None, 'video_id': None}
    return {'status': 'matched', 'photo_id': str(photo_id),
            'plate': next(iter(plates)), 'video_id': str(video_id)}


class ApproveAccident(ContractModel):
    review_id: UUID
    expected_content_revision: int = Field(ge=0)
    amount_minor: int = Field(strict=True, gt=0, le=100_000_000)
    amendment_reason: str = Field(default='', max_length=1500)


class SendAccidentNotifications(ContractModel):
    review_id: UUID
    expected_content_revision: int = Field(ge=0)
    confirm_send: bool


class RepairRange(ContractModel):
    minimum_minor: int = Field(strict=True, ge=0, le=100_000_000)
    maximum_minor: int = Field(strict=True, ge=0, le=100_000_000)


class EditAccidentEstimate(ContractModel):
    review_id: UUID
    expected_content_revision: int = Field(ge=0)
    minimum_minor: int = Field(strict=True, ge=0, le=100_000_000)
    maximum_minor: int = Field(strict=True, ge=0, le=100_000_000)
    line_items: list[RepairRange] = Field(max_length=12)


def insurance_matches(assessment, incident_at, lookup_record=insurance_lookup):
    results = []
    for plate in assessment.media.plates:
        entry = {'vehicle': plate.vehicle, 'role': plate.role, 'plate': plate.plate,
                 'status': 'unavailable', 'data': {}, 'synthetic': True}
        value = normalize_plate(plate.plate or '')
        countries = [country for country, pattern in PATTERNS.items() if re.fullmatch(pattern, value)]
        if plate.legibility == 'readable' and len(countries) == 1 and incident_at:
            lookup = lookup_record(value, countries[0], incident_at.date())
            entry.update(status=lookup.status, data=lookup.data, reason=lookup.reason)
        else:
            entry['reason'] = 'incident_date_required' if not incident_at else 'plate_unreadable'
        results.append(entry)
    return results


def blocking_reasons(assessment, matches):
    blockers = list(assessment.missing_information)
    if not assessment.selected_video_id:
        blockers.append('Aucune vidéo ne correspond avec suffisamment de certitude aux photos.')
    if not assessment.insured_vehicle or not assessment.repair_estimate or assessment.repair_estimate.maximum_minor <= 0:
        blockers.append('Le véhicule assuré et une estimation de ses dommages doivent être établis.')
    if (assessment.media.liability.likely_responsible != 'third_party' or not assessment.at_fault_vehicle
            or assessment.at_fault_vehicle == assessment.insured_vehicle):
        blockers.append('La responsabilité d’un tiers doit être établie avant de préparer son recours.')
    if not any(m['vehicle'] == assessment.at_fault_vehicle and m['status'] == 'matched' for m in matches):
        blockers.append('La plaque lisible du tiers doit correspondre à un assureur dans la base fictive.')
    return list(dict.fromkeys(blockers))


class ClaimNotificationGateway:
    """Simulations are explicit; actual demo emails require a configured test inbox."""
    def send(self, row):
        if row['mode'] == 'simulated':
            return 'simulated:' + str(row['id'])
        if row['channel'] == 'email':
            mailer = ResendMailer()
            return mailer.send(mailer.payload(row), 'claim-notification:' + str(row['id']))
        from claim_api.sms_link import TwilioSmsConfig
        from twilio.rest import Client
        from twilio.http.http_client import TwilioHttpClient
        from twilio.base.exceptions import TwilioRestException
        config = TwilioSmsConfig.from_env()
        try:
            result = Client(config.account_sid, config.auth_token, http_client=TwilioHttpClient(timeout=8)).messages.create(
                messaging_service_sid=config.messaging_service_sid,
                to=validate_e164(row['recipient']), body=row['body'])
        except TwilioRestException as error:
            raise CorrespondenceError('sms_rejected' if error.status < 500 else 'sms_delivery_unknown',
                                      uncertain=error.status >= 500) from None
        except Exception:
            raise CorrespondenceError('sms_delivery_unknown', uncertain=True) from None
        if not isinstance(result.sid, str) or not re.fullmatch(r'SM[0-9a-fA-F]{32}', result.sid):
            raise CorrespondenceError('sms_delivery_unknown', uncertain=True)
        return result.sid


class AccidentJourney:
    def __init__(self, repository, analyzer=None, gateway=None):
        self.repository = repository
        self.reference_data = ReferenceData(repository)
        self.analyzer = analyzer or AstraAccidentAnalyzer(cache=PostgresVideoRepository(repository))
        self.gateway = gateway or ClaimNotificationGateway()

    def analyze(self, job):
        actor, case_id = job['actor_id'], job['case_id']
        case = self.repository.get_case(actor, case_id)
        if not case or case.content_revision != job['content_revision']:
            self.repository.finish(job, 'stale'); return
        if case.voice_session and case.voice_session.status == 'urgent_human_handoff':
            self.repository.finish(job, 'needs_action', error='urgent_human_handoff'); return
        with self.repository._connection() as conn:
            existing = conn.execute('select id from public.case_accident_reviews where case_id=%s and content_revision=%s',
                                    (case_id, case.content_revision)).fetchone()
        if existing:
            self.repository.finish(job, 'ready'); return
        entries = self.reference_data.video_catalogue()
        if not entries:
            raise AnalysisError('catalogue_empty', 'Le catalogue vidéo est vide.')
        assessment = self.analyzer.analyze(case, entries)
        matches = insurance_matches(assessment, case.intake.incident_at, self.reference_data.insurance_lookup)
        blockers = blocking_reasons(assessment, matches)
        with self.repository._connection() as conn:
            current = conn.execute('select * from public.cases where id=%s and created_by_user_id=%s for update',
                                   (case_id, actor)).fetchone()
            lease = conn.execute('select lease_id from public.case_media_workflows where id=%s for update', (job['id'],)).fetchone()
            if not current or current['content_revision'] != case.content_revision:
                conn.execute("update public.case_media_workflows set status='stale',lease_id=null,lease_until=null where id=%s and lease_id=%s", (job['id'],job['lease_id'])); return
            if not lease or lease['lease_id'] != job['lease_id']:
                return
            conn.execute('''insert into public.case_accident_reviews
                (case_id,content_revision,model,assessment,insurance_matches,blockers,status)
                values (%s,%s,%s,%s,%s,%s,%s) on conflict (case_id,content_revision) do nothing''',
                (case_id,case.content_revision,self.analyzer.model,Jsonb(assessment.model_dump(mode='json')),
                 Jsonb(matches),Jsonb(blockers),'needs_information' if blockers else 'awaiting_review'))
            conn.execute("update public.cases set status=%s,updated_at=now() where id=%s",
                         ('collecting' if blockers else 'review_ready', case_id))
            conn.execute('''update public.case_media_workflows set status='ready',insurance_matches=%s,
                error_code=null,lease_id=null,lease_until=null,updated_at=now() where id=%s and lease_id=%s''',
                (Jsonb(matches),job['id'],job['lease_id']))

    def notification_routes(self, conn, case_id, review):
        """Resolve delivery once for both preview and approval, without sending."""
        configured_mode = os.getenv('CLAIM_NOTIFICATION_MODE', 'simulated')
        mode = configured_mode if configured_mode in {'simulated', 'live'} else 'simulated'
        routes = [{'channel': channel, 'recipient': None, 'mode': mode,
                   'status': 'ready', 'reason': None, 'error_code': None, 'http_status': 409}
                  for channel in ('sms', 'email')]
        sms, email = routes

        def unavailable(route, code, reason, http_status=409):
            route.update(status='unavailable', reason=reason, error_code=code, http_status=http_status)

        # The recipient of the first successfully issued invitation is authoritative.
        phone = conn.execute("""select recipient from public.case_messages where case_id=%s
            and channel='sms' and provider=%s and status in ('accepted','sent','delivered')
            order by created_at, id limit 1""", (case_id, SMS_LINK_PROVIDER)).fetchone()
        try:
            sms['recipient'] = validate_e164(phone['recipient']) if phone else None
        except (ValueError, TypeError):
            pass
        if not sms['recipient']:
            unavailable(sms, 'insured_contact_missing', 'Le lien privé doit avoir été envoyé à l’assuré.')
        assessment = review.get('assessment', {}) if review else {}
        match = next((m for m in (review.get('insurance_matches', []) if review else [])
                      if m['vehicle'] == assessment.get('at_fault_vehicle') and m['status'] == 'matched'), None)
        if mode == 'live':
            email['recipient'] = (os.getenv('CLAIM_EMAIL_TO', '').strip()
                                  or os.getenv('CLAIM_DEMO_INSURER_EMAIL', '').strip() or None)
        elif match:
            email['recipient'] = match['data'].get('insurer_email')
        if not match:
            unavailable(email, 'insurer_contact_missing', 'L’assureur du tiers doit être identifié avant validation.')
        elif not email['recipient']:
            unavailable(email, 'notification_not_configured', 'La boîte de réception de l’assureur doit être configurée.', 503)
        if mode == 'live':
            from claim_api.sms_link import TwilioSmsConfig
            try:
                TwilioSmsConfig.from_env()
            except ValueError:
                if sms['status'] == 'ready':
                    unavailable(sms, 'notification_not_configured', 'Configurez le service SMS avant l’envoi réel.', 503)
            if email['status'] == 'ready':
                try:
                    ResendMailer().payload({'recipient': email['recipient'], 'subject': 'Validation', 'body': 'Validation'})
                except ValueError:
                    unavailable(email, 'notification_not_configured', 'Configurez le service email et une boîte de test valide pour l’assureur fictif.', 503)
        if configured_mode not in {'simulated', 'live'}:
            for route in routes:
                unavailable(route, 'notification_mode_invalid', 'Mode de notification invalide.', 503)
        return routes

    @staticmethod
    def notification_preview(routes):
        return [{key: (mask_e164(value) if key == 'recipient' and route['channel'] == 'sms' and value else value)
                 for key, value in route.items() if key not in {'error_code', 'http_status'}}
                for route in routes]

    def view(self, actor, case_id):
        case = self.repository.get_case(actor, case_id)
        if not case:
            raise CaseNotFoundError
        with self.repository._connection() as conn:
            review = conn.execute('select * from public.case_accident_reviews where case_id=%s and content_revision=%s',
                                  (case_id,case.content_revision)).fetchone()
            notifications = conn.execute('''select n.* from public.case_claim_notifications n
                join public.case_accident_reviews r on r.id=n.review_id
                where n.case_id=%s and r.content_revision=%s order by n.channel''',
                (case_id,case.content_revision)).fetchall()
            preview = self.notification_preview(self.notification_routes(conn, case_id, review))
            catalogue_count = conn.execute('select count(*) as n from public.accident_video_catalogue where active').fetchone()['n']
        for n in notifications:
            if n['channel'] == 'sms': n['recipient'] = mask_e164(n['recipient'])
        return {'content_revision':case.content_revision,'review':review,'notifications':notifications,
                'model':'gpt-6-astra','catalogue_count':catalogue_count,
                'notification_mode':os.getenv('CLAIM_NOTIFICATION_MODE','simulated'),
                'notification_preview': preview}

    def approve(self, actor, case_id, request):
        with self.repository._connection() as conn:
            case = conn.execute('select * from public.cases where id=%s and created_by_user_id=%s for update',
                                (case_id,actor)).fetchone()
            if not case: raise CaseNotFoundError
            if case['content_revision'] != request.expected_content_revision:
                raise StaleCaseError(case['state_version'])
            review = conn.execute('''select * from public.case_accident_reviews where id=%s and case_id=%s
                and content_revision=%s for update''',(request.review_id,case_id,case['content_revision'])).fetchone()
            if not review: raise SmsError('review_not_found',409,'La proposition a changé.')
            if review['status'] == 'approved':
                if review['approved_amount_minor'] != request.amount_minor:
                    raise SmsError('already_approved',409,'Le montant a déjà été validé.')
                return self.view(actor,case_id)
            assessment = AccidentAssessment.model_validate(review['assessment'])
            blockers = blocking_reasons(assessment,review['insurance_matches'])
            if blockers: raise SmsError('review_blocked',409,'Complétez les informations demandées avant validation.')
            estimate = assessment.repair_estimate
            suggested = (estimate.minimum_minor + estimate.maximum_minor) // 2
            if request.amount_minor != suggested and not request.amendment_reason.strip():
                raise SmsError('amendment_reason_required',422,'Indiquez la raison de la modification du montant.')
            conn.execute('''update public.case_accident_reviews set status='approved',approved_amount_minor=%s,
                approved_by=%s,approved_at=now(),amendment_reason=%s where id=%s''',
                (request.amount_minor,actor,request.amendment_reason.strip(),review['id']))
            conn.execute("update public.cases set status='approved',state_version=state_version+1,updated_at=now() where id=%s",(case_id,))
            conn.execute('''insert into public.audit_events(case_id,actor_user_id,event_type,state_version_before,state_version_after,
                content_revision_before,content_revision_after,metadata_json) values (%s,%s,'case.accident_approved',%s,%s,%s,%s,%s)''',
                (case_id,actor,case['state_version'],case['state_version']+1,case['content_revision'],case['content_revision'],
                 Jsonb({'review_id':str(review['id']),'amount_minor':request.amount_minor})))
        return self.view(actor,case_id)

    def edit_estimate(self, actor, case_id, request: EditAccidentEstimate):
        with self.repository._connection() as conn:
            case = conn.execute('select * from public.cases where id=%s and created_by_user_id=%s for update',
                                (case_id, actor)).fetchone()
            if not case:
                raise CaseNotFoundError
            if case['content_revision'] != request.expected_content_revision:
                raise StaleCaseError(case['state_version'])
            review = conn.execute('''select * from public.case_accident_reviews where id=%s and case_id=%s
                and content_revision=%s for update''',
                (request.review_id, case_id, case['content_revision'])).fetchone()
            if not review or review['status'] != 'awaiting_review':
                raise SmsError('estimate_not_editable', 409, 'Le chiffrage ne peut plus être modifié.')
            assessment = AccidentAssessment.model_validate(review['assessment'])
            old = assessment.repair_estimate
            if not old:
                raise SmsError('estimate_missing', 409, 'Aucun chiffrage à modifier.')
            if len(request.line_items) != len(old.line_items):
                raise SmsError('estimate_changed', 409, 'Les postes de réparation ont changé.')
            if request.maximum_minor < request.minimum_minor or request.maximum_minor == 0:
                raise SmsError('estimate_invalid', 422, 'Vérifiez la fourchette du chiffrage.')
            items = [{**item.model_dump(), **range_.model_dump()}
                     for item, range_ in zip(old.line_items, request.line_items)]
            try:
                edited = RepairEstimate.model_validate({
                    **old.model_dump(), 'minimum_minor': request.minimum_minor,
                    'maximum_minor': request.maximum_minor, 'line_items': items,
                })
            except ValueError:
                raise SmsError('estimate_invalid', 422, 'Le total doit être égal à la somme des postes.') from None
            assessment.repair_estimate = edited
            conn.execute('update public.case_accident_reviews set assessment=%s where id=%s',
                         (Jsonb(assessment.model_dump(mode='json')), review['id']))
            conn.execute('update public.cases set state_version=state_version+1,updated_at=now() where id=%s',
                         (case_id,))
            conn.execute('''insert into public.audit_events(case_id,actor_user_id,event_type,state_version_before,state_version_after,
                content_revision_before,content_revision_after,metadata_json)
                values (%s,%s,'case.accident_estimate_edited',%s,%s,%s,%s,%s)''',
                (case_id, actor, case['state_version'], case['state_version'] + 1,
                 case['content_revision'], case['content_revision'],
                 Jsonb({'review_id': str(review['id']),
                        'before': {'minimum_minor': old.minimum_minor, 'maximum_minor': old.maximum_minor},
                        'after': {'minimum_minor': edited.minimum_minor, 'maximum_minor': edited.maximum_minor}})))
        return self.view(actor, case_id)

    def send_notifications(self, actor, case_id, request: SendAccidentNotifications):
        if not request.confirm_send:
            raise SmsError('send_confirmation_required', 422, 'Confirmez l’envoi du SMS et de l’email.')
        queued = False
        with self.repository._connection() as conn:
            case = conn.execute('select * from public.cases where id=%s and created_by_user_id=%s for update',
                                (case_id, actor)).fetchone()
            if not case:
                raise CaseNotFoundError
            if case['content_revision'] != request.expected_content_revision:
                raise StaleCaseError(case['state_version'])
            review = conn.execute('''select * from public.case_accident_reviews where id=%s and case_id=%s
                and content_revision=%s for update''',
                (request.review_id, case_id, case['content_revision'])).fetchone()
            if not review or review['status'] != 'approved' or not review['approved_amount_minor']:
                raise SmsError('review_not_approved', 409, 'Validez le montant avant l’envoi.')
            existing = conn.execute('select id from public.case_claim_notifications where review_id=%s limit 1',
                                    (review['id'],)).fetchone()
            if existing:
                return self.view(actor, case_id)
            assessment = AccidentAssessment.model_validate(review['assessment'])
            match = next(m for m in review['insurance_matches']
                         if m['vehicle'] == assessment.at_fault_vehicle and m['status'] == 'matched')
            routes = self.notification_routes(conn, case_id, review)
            for route in routes:
                if route['status'] != 'ready':
                    raise SmsError(route['error_code'], route['http_status'], route['reason'])
            recipient, email_recipient = routes[0]['recipient'], routes[1]['recipient']
            mode = routes[0]['mode']
            amount_minor = review['approved_amount_minor']
            estimate = assessment.repair_estimate
            amount = f'{amount_minor / 100:.2f} EUR'
            sms = claim_approval_sms(case, assessment, amount_minor)
            body = (f'Démonstration — dossier synthétique {case_id}\n\n'
                    f'Assureur tiers de la base fictive : {match["data"]["insurer_name"]}\n'
                    f'Plaque observée : {match["plate"]}\n'
                    f'Véhicule assuré : {assessment.insured_vehicle}\n'
                    f'Montant validé par le gestionnaire : {amount}\n'
                    f'Fourchette indicative initiale : {estimate.minimum_minor/100:.2f}–{estimate.maximum_minor/100:.2f} EUR\n'
                    f'Motif de modification : {review["amendment_reason"] or "Montant proposé validé"}\n\n'
                    f'{assessment.media.summary}\n\nResponsabilité probable : {assessment.media.liability.reasoning}\n'
                    f'Dommages : {estimate.assumptions}\n\n'
                    'Sources : photos fournies par l’assuré et vidéo du catalogue synthétique. Aucune demande de paiement réelle.')
            for channel, to, subject, text in [('sms', recipient, 'Validation de votre dossier', sms),
                    ('email', email_recipient, f'[Démonstration] Recours — {str(case_id)[:8]}', body)]:
                conn.execute('''insert into public.case_claim_notifications
                    (review_id,case_id,channel,recipient,subject,body,mode) values (%s,%s,%s,%s,%s,%s,%s)
                    on conflict (review_id,channel) do nothing''',
                    (review['id'], case_id, channel, to, subject, text, mode))
            queued = True
        # Persist both messages before contacting providers; retries cannot create duplicates.
        if queued:
            self.deliver(case_id)
        return self.view(actor, case_id)

    def deliver(self, case_id=None):
        processed = False
        for _ in range(2):
            with self.repository._connection() as conn:
                # A timeout after sending is uncertain, never an invitation to duplicate an SMS.
                conn.execute("""update public.case_claim_notifications set status='unknown',error_code='delivery_reconciliation_required'
                    where status='sending' and started_at < now()-interval '2 minutes'""")
                row = conn.execute('''select n.*,c.content_revision as current_revision,r.content_revision as approved_revision
                    from public.case_claim_notifications n join public.cases c on c.id=n.case_id
                    join public.case_accident_reviews r on r.id=n.review_id where n.status='queued'
                    and (%s::uuid is null or n.case_id=%s) order by n.created_at,n.id
                    for update of n skip locked limit 1''',(case_id,case_id)).fetchone()
                if not row: break
                processed = True
                if row['current_revision'] != row['approved_revision']:
                    conn.execute("update public.case_claim_notifications set status='cancelled',error_code='case_changed' where id=%s",(row['id'],))
                    continue
                conn.execute("update public.case_claim_notifications set status='sending',started_at=now(),updated_at=now() where id=%s",(row['id'],))
            try:
                provider_id = self.gateway.send(row)
                status,error = ('simulated' if row['mode']=='simulated' else 'sent'),None
            except CorrespondenceError as failure:
                status,error,provider_id = ('unknown' if failure.uncertain else 'failed'),failure.code,None
            except Exception:
                status,error,provider_id = 'unknown','delivery_reconciliation_required',None
            with self.repository._connection() as conn:
                conn.execute('''update public.case_claim_notifications set status=%s,error_code=%s,provider_message_id=%s,
                    updated_at=now() where id=%s and status='sending' ''',(status,error,provider_id,row['id']))
                conn.execute('''update public.cases c set status='sent',state_version=state_version+1,updated_at=now()
                    where c.id=%s and c.content_revision=%s and c.status='approved'
                    and (select count(*) from public.case_claim_notifications where review_id=%s and status in ('sent','simulated'))=2''',
                    (row['case_id'],row['approved_revision'],row['review_id']))
        return processed
