"""End-to-end claims journey on PostgreSQL, with only external providers replaced."""
import hashlib
import json
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from sqlalchemy import text

from claim_api.accident_journey import AccidentJourney, ApproveAccident, EditAccidentEstimate, SendAccidentNotifications, blocking_reasons, claim_approval_sms, insurance_matches
from claim_api.analysis import AnalysisError
from claim_api.astra_analysis import AstraAccidentAnalyzer, AccidentAssessment, catalogue
from claim_api.auth import AuthenticatedUser, get_current_user
from claim_api.case_service import CaseNotFoundError, StaleCaseError
from claim_api.correspondence import CorrespondenceError
from claim_api.demo_media import MEDIA_ROOT
from claim_api.evidence_service import EvidenceService
from claim_api.insured_portal import InsuredPortalService
from claim_api.main import create_app
from claim_api.media_workflow import MediaWorkflow, MediaWorkflowRepository
from claim_api.models import CreateEvidenceUploadIntentRequest, FinalizeEvidenceRequest
from claim_api.routes.accident_journey import journey as journey_dependency
from claim_api.sms_link import SmsLinkService
from claim_api.sms_service import SmsError
from claim_api.voice_repository import VoiceIntakeRepository
from test_evidence_service import FakeStorage
from test_insured_portal import portal_db
from test_voice_intake import request as voice_request


def assessment_for(case, entries=None):
    entries = entries or catalogue()
    vid = entries[0]['id']; photo = str(case.evidence[0].id)
    citation = {'evidence_id':vid,'timestamp_seconds':entries[0]['frames'][0]['seconds']}
    pc = {'evidence_id':photo,'timestamp_seconds':None}
    estimate = {'minimum_minor':200000,'maximum_minor':400000,'currency':'EUR','assumptions':'Pièces et main-d’œuvre, dommages cachés à confirmer.'}
    findings = lambda vehicle,role,plate: {'vehicle':vehicle,'role':role,'plate':plate,'legibility':'readable',
            'description':'Plaque lisible','confidence':'high','citations':[citation]}
    return AccidentAssessment.model_validate({
        'selected_video_id':vid,'video_candidates':[{'video_id':v['id'],'compatibility':'strong' if i==0 else 'incompatible','reasons':'Concordance des impacts et véhicule.' if i==0 else 'Autre véhicule et autre impact.'} for i,v in enumerate(entries)],
        'match_reasoning':'Les photos et la première vidéo montrent le même véhicule et le même impact.',
        'insured_vehicle':'Peugeot','at_fault_vehicle':'BMW','repair_estimate':estimate,'missing_information':[],
        'media':{'analyzed_evidence_ids':[str(e.id) for e in case.evidence]+[vid],'summary':'La BMW heurte la Peugeot à l’arrêt.',
            'observations':[{'description':'Contact visible','confidence':'high','citations':[citation]}],
            'plates':[findings('Peugeot','insured','FR-482-KL'),findings('BMW','third_party','AB12 CDE')],
            'damages':[{'vehicle':v,'description':'Pare-chocs endommagé','confidence':'medium','citations':[pc if i==0 else citation],
                'affected_parts':['pare-chocs'],'severity':'moderate','accident_link':'consistent','estimate':estimate if i==0 else None} for i,v in enumerate(['Peugeot','BMW'])],
            'liability':{'likely_responsible':'third_party','reasoning':'La BMW entre en contact avec le véhicule arrêté.',
                'confidence':'high','citations':[citation],'limitations':['Hypothèse à valider.'],'requires_human_review':True,
                'vehicle_assessments':[{'vehicle':v,'role':r,'assessment':a,'reasoning':'Chronologie visible.',
                    'confidence':'high','citations':[citation]} for v,r,a in [('Peugeot','insured','no_visible_contribution'),('BMW','third_party','likely_responsible')]]},
            'cross_evidence_consistency':'Impacts cohérents','limitations':['Analyse d’images espacées de 250 ms.']}})


def test_approval_sms_uses_only_stored_amounts_and_midpoints():
    assessment = SimpleNamespace(insured_vehicle='Peugeot grise', repair_estimate=SimpleNamespace(
        minimum_minor=350000, maximum_minor=700000, line_items=[
            SimpleNamespace(label=label, minimum_minor=low, maximum_minor=high)
            for label, low, high in [
                ('Feu arrière gauche', 45000, 85000), ('Aile et logement du feu', 110000, 230000),
                ('Pare-chocs et fixations', 80000, 160000), ('Peinture et main-d’œuvre', 115000, 225000),
            ]]))
    sms = claim_approval_sms({'intake_json': {'insured_name': 'Camille', 'insured_reference': 'CLM-2026-0843'}}, assessment, 525000)
    for fragment in ('Camille', 'CLM-2026-0843', '5 250 €', '650 €', '1 700 €', '1 200 €', '1 700 €', 'devis du réparateur'):
        assert fragment in sms
    assert 'rembours' not in sms and 'pris en charge' not in sms and 'garage' not in sms
    amended = claim_approval_sms({'intake_json': {}}, assessment, 500000)
    assert 'montant retenu' in amended and '5 000 €' in amended


class Storage(FakeStorage):
    def read_object(self,path,limit):
        return self.fixture_bytes.get(path,self.object_bytes.get(path,b''))


class Model:
    model = 'gpt-6-astra'
    def __init__(self): self.calls = 0
    def analyze(self,case,entries):
        self.calls += 1
        return assessment_for(case,entries)


@pytest.fixture
def journey_case(portal_db,monkeypatch):
    url,engine,actor,*_ = portal_db
    calls=[]
    class SmsGateway:
        def send(self,id,number,body):
            calls.append((id,number,body)); return 'SM'+uuid4().hex
    request=voice_request(event_type='end_of_call_report',overrides={'insured_vehicle':('Peugeot grise','Ma voiture est une Peugeot grise.'),'insured_plate':('FR-482-KL','Ma plaque est FR-482-KL.'),'narrative':('Ma Peugeot était déjà immobile le long du trottoir avant le début de la vidéo, sans manœuvre en cours. Une BMW en recul a heurté son arrière gauche. Je fournis une vue d’ensemble et un détail des dommages.', 'Ma Peugeot était déjà immobile le long du trottoir avant le début de la vidéo, sans manœuvre en cours. Une BMW en recul a heurté son arrière gauche. Je fournis une vue d’ensemble et un détail des dommages.')}).model_copy(update={'provider_call_id':str(uuid4()),'mode':'live'})
    voice=VoiceIntakeRepository(url).ingest(actor,request)
    case_id=voice.session.case_id
    grants=portal_db[5]
    sms=SmsLinkService(url,grants,caller_lookup=SimpleNamespace(candidate=lambda _: '+33612345678'),gateway=SmsGateway())
    monkeypatch.setenv('SMS_LINK_DELIVERY_MODE','twilio')
    monkeypatch.setattr('claim_api.routes.sms_link.get_sms_link_service',lambda:sms)
    from claim_api.routes.voice import _auto_fake_follow_up
    _auto_fake_follow_up(actor,case_id)
    _auto_fake_follow_up(actor,case_id)
    assert len(calls)==1
    invitation=sms.list_links(actor,case_id)[0]
    assert invitation.status=='accepted'
    token=sms.link(actor,case_id,invitation.id)['url'].split('#token=')[1]
    portal=InsuredPortalService(url)
    session=portal.exchange(token)['session_token']
    storage=Storage();repo=MediaWorkflowRepository(url);model=Model()
    journey=AccidentJourney(repo,analyzer=model);flow=MediaWorkflow(repo,journey)
    service=EvidenceService(repo,storage,on_media_received=flow.received)
    monkeypatch.setattr(portal,'_evidence_service',lambda:service)
    for photo in sorted((MEDIA_ROOT/'g1').glob('*.png')):
        data=photo.read_bytes()
        version=portal.summary(session)['state_version']
        intent=portal.create_upload_intent(session,CreateEvidenceUploadIntentRequest(expected_state_version=version,
            kind='damage_photo',filename=photo.name,mime_type='image/png',byte_size=len(data),client_sha256=hashlib.sha256(data).hexdigest()))
        storage.upload_fixture(intent.storage_path,data,'image/png')
        portal.finalize_upload(session,FinalizeEvidenceRequest(storage_path=intent.storage_path,expected_state_version=version,
            kind='damage_photo',client_sha256=hashlib.sha256(data).hexdigest()))
    assert model.calls==0 # ingestion does not block on a long model request
    monkeypatch.setenv('CLAIM_NOTIFICATION_MODE','simulated')
    return repo,journey,flow,actor,case_id,model,portal,session,engine


def test_phone_sms_private_photo_video_match_amended_review_and_both_notifications(journey_case):
    repo,journey,flow,actor,case_id,model,portal,session,engine=journey_case
    assert flow.tick(case_id)
    view=journey.view(actor,case_id)
    assert view['review']['status']=='awaiting_review'
    assert not view['notifications'] and model.calls==1
    assert all(p['status'] == 'ready' and p['mode'] == 'simulated' for p in view['notification_preview'])
    assert view['notification_preview'][0]['recipient'].endswith('5678')
    assert '+33612345678' not in str(view['notification_preview'])
    assert view['review']['insurance_matches'][1]['data']['insurer_email'].endswith('.test')
    app=create_app();app.dependency_overrides[get_current_user]=lambda:AuthenticatedUser(id=str(actor))
    app.dependency_overrides[journey_dependency]=lambda:journey
    client=TestClient(app)
    payload={'review_id':str(view['review']['id']),'expected_content_revision':view['content_revision'],
             'amount_minor':350000,'amendment_reason':'Main-d’œuvre revue par le gestionnaire.'}
    result=client.post(f'/v1/cases/{case_id}/journey/approve',json=payload)
    assert result.status_code==200,result.text
    updated=result.json()
    assert updated['review']['status']=='approved'
    assert updated['notifications']==[]
    assert repo.get_case(actor,case_id).status=='approved'
    assert not journey.deliver(case_id)
    result=client.post(f'/v1/cases/{case_id}/journey/send',json={
        'review_id':payload['review_id'],'expected_content_revision':payload['expected_content_revision'],
        'confirm_send':True})
    assert result.status_code==200,result.text
    updated=result.json()
    assert {n['channel'] for n in updated['notifications']}=={'sms','email'}
    assert all(n['status']=='simulated' for n in updated['notifications'])
    assert '3500.00 EUR' in next(n['body'] for n in updated['notifications'] if n['channel'] == 'email')
    sms_body = next(n['body'] for n in updated['notifications'] if n['channel'] == 'sms')
    assert '3 500 €' in sms_body and 'devis du réparateur' in sms_body
    assert 'pris en charge' not in sms_body
    assert updated['review']['approved_by']==str(actor)
    assert repo.get_case(actor,case_id).status=='sent'
    assert client.post(f'/v1/cases/{case_id}/journey/approve',json=payload).status_code==200
    assert client.post(f'/v1/cases/{case_id}/journey/send',json={
        'review_id':payload['review_id'],'expected_content_revision':payload['expected_content_revision'],
        'confirm_send':True}).status_code==200
    assert not flow.tick(case_id) and model.calls==1
    with engine.connect() as conn:
        assert conn.execute(text('select count(*) from public.case_claim_notifications where case_id=:id'),{'id':case_id}).scalar()==2
    outsider=uuid4()
    with pytest.raises(CaseNotFoundError): journey.view(outsider,case_id)


def test_final_sms_uses_first_post_call_sms_recipient(journey_case):
    repo, journey, flow, actor, case_id, _, _, _, engine = journey_case
    with engine.begin() as conn:
        conn.execute(text('''insert into public.case_messages
            (case_id,channel,mode,recipient,body,status,provider,idempotency_key,payload_hash)
            values (:case_id,'whatsapp','mock','+33687654321','Later chat','accepted',
                    'claimroom.fake_whatsapp','later-chat',:hash)'''),
            {'case_id': case_id, 'hash': 'b' * 64})
    assert flow.tick(case_id)
    view = journey.view(actor, case_id)
    journey.approve(actor, case_id, ApproveAccident(
        review_id=view['review']['id'], expected_content_revision=view['content_revision'],
        amount_minor=300000))
    journey.send_notifications(actor, case_id, SendAccidentNotifications(
        review_id=view['review']['id'], expected_content_revision=view['content_revision'],
        confirm_send=True))
    with engine.connect() as conn:
        recipient = conn.execute(text('''select recipient from public.case_claim_notifications
            where case_id=:case_id and channel='sms' '''), {'case_id': case_id}).scalar_one()
    assert recipient == '+33612345678'


def test_edit_estimate_recalculates_line_totals_before_approval(journey_case):
    repo, journey, flow, actor, case_id, _, _, _, _ = journey_case
    assert flow.tick(case_id)
    view = journey.view(actor, case_id)
    review = view['review']
    estimate = review['assessment']['repair_estimate']
    estimate['line_items'] = [
        {'label': 'Pare-chocs', 'minimum_minor': 120000, 'maximum_minor': 220000},
        {'label': 'Peinture', 'minimum_minor': 80000, 'maximum_minor': 180000},
    ]
    with repo._connection() as conn:
        conn.execute('update public.case_accident_reviews set assessment=%s where id=%s',
                     (Jsonb(review['assessment']), review['id']))
    request = EditAccidentEstimate(review_id=review['id'],
        expected_content_revision=view['content_revision'], minimum_minor=230000,
        maximum_minor=450000, line_items=[
            {'minimum_minor': 130000, 'maximum_minor': 250000},
            {'minimum_minor': 100000, 'maximum_minor': 200000},
        ])
    result = journey.edit_estimate(actor, case_id, request)
    edited = result['review']['assessment']['repair_estimate']
    assert (edited['minimum_minor'], edited['maximum_minor']) == (230000, 450000)
    assert [item['label'] for item in edited['line_items']] == ['Pare-chocs', 'Peinture']
    assert result['notifications'] == []
    with pytest.raises(SmsError, match='total'):
        journey.edit_estimate(actor, case_id, request.model_copy(update={'maximum_minor': 440000}))
    approved = journey.approve(actor, case_id, ApproveAccident(
        review_id=review['id'], expected_content_revision=view['content_revision'],
        amount_minor=340000))
    assert approved['review']['approved_amount_minor'] == 340000
    with pytest.raises(SmsError, match='plus être modifié'):
        journey.edit_estimate(actor, case_id, request)


def test_live_notifications_use_insured_sms_and_configured_test_email(journey_case, monkeypatch):
    repo, journey, flow, actor, case_id, _, _, _, _ = journey_case
    monkeypatch.setenv('CLAIM_NOTIFICATION_MODE', 'live')
    # Reserved example domain; the Gateway below captures every delivery.
    monkeypatch.setenv('CLAIM_EMAIL_TO', 'claims-handler@example.com')
    monkeypatch.setenv('RESEND_API_KEY', 're_test')
    monkeypatch.setenv('CLAIMROOM_EMAIL_FROM', 'onboarding@resend.dev')
    monkeypatch.setenv('CLAIMROOM_EMAIL_REPLY_TO', 'claims-handler@example.com')
    monkeypatch.setattr('claim_api.sms_link.TwilioSmsConfig.from_env', lambda: object())
    sent = []

    class Gateway:
        def send(self, row):
            sent.append((row['channel'], row['recipient']))
            return 'provider-id'

    journey.gateway = Gateway()
    assert flow.tick(case_id)
    view = journey.view(actor, case_id)
    assert view['notification_preview'][1]['recipient'] == 'claims-handler@example.com'
    assert all(p['status'] == 'ready' for p in view['notification_preview'])
    result = journey.approve(actor, case_id, ApproveAccident(
        review_id=view['review']['id'], expected_content_revision=view['content_revision'],
        amount_minor=300000))
    assert sent == [] and result['notifications'] == []
    result = journey.send_notifications(actor, case_id, SendAccidentNotifications(
        review_id=view['review']['id'], expected_content_revision=view['content_revision'], confirm_send=True))
    assert sorted(sent) == [('email', 'claims-handler@example.com'), ('sms', '+33612345678')]
    assert {n['status'] for n in result['notifications']} == {'sent'}


def test_changed_photos_or_statement_invalidate_review_and_queue_automatically(journey_case):
    repo,journey,flow,actor,case_id,model,portal,session,_=journey_case
    flow.tick(case_id); prior=journey.view(actor,case_id)
    summary=portal.summary(session)
    portal.correct(session,summary['state_version'],'location','Lyon','Correction de l’assuré')
    assert journey.view(actor,case_id)['review'] is None
    with pytest.raises(StaleCaseError):
        journey.approve(actor,case_id,ApproveAccident(review_id=prior['review']['id'],
            expected_content_revision=prior['content_revision'],amount_minor=300000))
    assert flow.tick(case_id); assert model.calls==2
    assert journey.view(actor,case_id)['review']['status']=='awaiting_review'


def test_priority_question_reaches_insured_and_answer_restarts_analysis(journey_case):
    repo,journey,flow,actor,case_id,model,portal,session,_=journey_case
    def incomplete(case, entries):
        result=assessment_for(case, entries)
        result.missing_information=['Votre véhicule était-il déjà à l’arrêt avant le choc ?']
        return result
    journey.analyzer=SimpleNamespace(model='gpt-6-astra',analyze=incomplete)
    assert flow.tick(case_id)
    view=journey.view(actor,case_id)
    assert view['review']['status']=='needs_information'
    summary=portal.summary(session)
    assert summary['analysis_requests']==view['review']['assessment']['missing_information']
    assert 'insurance_matches' not in summary
    from claim_api.sms_service import SmsError
    with pytest.raises(SmsError,match='Complétez'):
        journey.approve(actor,case_id,ApproveAccident(review_id=view['review']['id'],
            expected_content_revision=view['content_revision'],amount_minor=300000))
    portal.correct(session,summary['state_version'],'narrative',
                   'Ma Peugeot était déjà à l’arrêt avant que la BMW la heurte.', 'Complément demandé dans le chat')
    journey.analyzer=model
    assert flow.tick(case_id)
    assert journey.view(actor,case_id)['review']['status']=='awaiting_review'
    assert portal.summary(session)['analysis_requests']==[]


def test_unknown_send_is_not_duplicated_and_other_channel_continues(journey_case):
    repo,journey,flow,actor,case_id,model,portal,session,engine=journey_case
    flow.tick(case_id); view=journey.view(actor,case_id); attempts=[]
    class Gateway:
        def send(self,row):
            attempts.append(row['channel'])
            if row['channel']=='sms': raise CorrespondenceError('timeout',uncertain=True)
            return 'email-id'
    journey.gateway=Gateway()
    result=journey.approve(actor,case_id,ApproveAccident(review_id=view['review']['id'],expected_content_revision=view['content_revision'],amount_minor=300000))
    assert attempts == [] and result['notifications'] == []
    result=journey.send_notifications(actor,case_id,SendAccidentNotifications(
        review_id=view['review']['id'],expected_content_revision=view['content_revision'],confirm_send=True))
    assert {n['channel']:n['status'] for n in result['notifications']}=={'sms':'unknown','email':'simulated'}
    assert repo.get_case(actor,case_id).status=='approved'
    assert not journey.deliver(case_id); assert sorted(attempts)==['email','sms']


def test_provider_contract_compares_all_videos_without_scenario_metadata(journey_case):
    repo,journey,flow,actor,case_id,model,portal,session,engine=journey_case
    case=repo.get_case(actor,case_id); response=assessment_for(case); payloads=[]
    class Response(BytesIO):
        def __enter__(self): return self
        def __exit__(self,*_): self.close()
    def provider(request,timeout):
        payload=json.loads(request.data);payloads.append(payload)
        return Response(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':response.model_dump_json()}]}]}).encode())
    data={hashlib.sha256(p.read_bytes()).hexdigest():p.read_bytes() for p in (MEDIA_ROOT/'g1').glob('*.png')}
    paths={e.storage_path:data[e.client_sha256] for e in case.evidence}
    storage=SimpleNamespace(read_object=lambda path,*_:paths[path])
    result=AstraAccidentAnalyzer(storage=storage,api_key='test',open_url=provider).analyze(case)
    payload=payloads[0]
    assert payload['model']=='gpt-6-astra' and payload['store'] is False
    assert 'scenario_id' not in json.dumps(payload) and 'Northbridge' not in json.dumps(payload)
    assert len([c for c in payload['input'][0]['content'] if c['type']=='input_image'])==62
    assert result.selected_video_id==response.selected_video_id


@pytest.mark.parametrize('failure',['foreign_source','timestamp','wrong_match','wrong_role','missing_vehicle'])
def test_invalid_model_findings_are_rejected(journey_case,failure):
    repo,journey,flow,actor,case_id,*_=journey_case
    case=repo.get_case(actor,case_id);entries=catalogue(); result=assessment_for(case)
    sources={str(e.id):None for e in case.evidence}
    sources.update({e['id']:{f['seconds'] for f in e['frames']} for e in entries})
    if failure=='foreign_source': result.media.plates[0].citations[0].evidence_id=uuid4()
    if failure=='timestamp': result.media.plates[0].citations[0].timestamp_seconds=999
    if failure=='wrong_match': result.selected_video_id=UUID(entries[1]['id'])
    if failure=='wrong_role': result.insured_vehicle='BMW'
    if failure=='missing_vehicle': result.media.damages=[]
    with pytest.raises(AnalysisError): AstraAccidentAnalyzer.validate(result,sources,{str(e.id) for e in case.evidence},{e['id'] for e in entries})


@pytest.mark.skipif(__import__('os').getenv('CLAIMROOM_LIVE_ASTRA_TEST') != '1', reason='Explicit paid API smoke test only')
def test_live_astra_pipeline_through_persisted_review_and_notifications(journey_case):
    repo,journey,flow,actor,case_id,model,portal,session,engine=journey_case
    from pathlib import Path
    import os
    case=repo.get_case(actor,case_id)
    data={hashlib.sha256(p.read_bytes()).hexdigest():p.read_bytes() for p in (MEDIA_ROOT/'g1').glob('*.png')}
    paths={e.storage_path:data[e.client_sha256] for e in case.evidence}
    journey.analyzer=AstraAccidentAnalyzer(storage=SimpleNamespace(read_object=lambda path,*_:paths[path]))
    assert flow.tick(case_id)
    view=journey.view(actor,case_id)
    assert view['review'],repo.snapshot(actor,case_id)
    assert view['review']['status']=='awaiting_review',view['review']['blockers']
    assert view['review']['assessment']['selected_video_id']==catalogue()[0]['id']
    estimate=view['review']['assessment']['repair_estimate']
    # Keep a non-secret local case identifier for browser verification of the same real analysis.
    artifact=os.getenv('CLAIMROOM_SMOKE_RESULT')
    if artifact:
        Path(artifact).write_text(json.dumps({'case_id':str(case_id),'actor_id':str(actor),'review_id':str(view['review']['id'])}))
    if os.getenv('CLAIMROOM_SMOKE_LEAVE_REVIEW')=='1':
        return
    result=journey.approve(actor,case_id,ApproveAccident(review_id=view['review']['id'],expected_content_revision=view['content_revision'],
        amount_minor=(estimate['minimum_minor']+estimate['maximum_minor'])//2))
    assert result['notifications']==[]
    result=journey.send_notifications(actor,case_id,SendAccidentNotifications(
        review_id=view['review']['id'],expected_content_revision=view['content_revision'],confirm_send=True))
    assert {n['status'] for n in result['notifications']}=={'simulated'}


class NotificationConnection:
    def __init__(self, phone): self.phone = phone
    def execute(self, query, params):
        assert "channel='sms'" in query and 'order by created_at, id limit 1' in query
        return SimpleNamespace(fetchone=lambda: {'recipient': self.phone} if self.phone else None)


def notification_review():
    return {'assessment': {'at_fault_vehicle': 'BMW'}, 'insurance_matches': [
        {'vehicle': 'BMW', 'status': 'matched', 'data': {'insurer_email': 'insurer@synthetic.test'}}]}


def test_notification_preview_never_invents_missing_sms_or_hides_invalid_live_config(monkeypatch):
    journey = AccidentJourney(SimpleNamespace())
    monkeypatch.setenv('CLAIM_NOTIFICATION_MODE', 'simulated')
    routes = journey.notification_routes(NotificationConnection(None), uuid4(), notification_review())
    assert routes[0]['status'] == 'unavailable' and routes[0]['recipient'] is None
    assert routes[0]['error_code'] == 'insured_contact_missing'
    assert routes[1]['recipient'] == 'insurer@synthetic.test'
    monkeypatch.setenv('CLAIM_NOTIFICATION_MODE', 'live')
    monkeypatch.delenv('CLAIM_EMAIL_TO', raising=False)
    monkeypatch.delenv('CLAIM_DEMO_INSURER_EMAIL', raising=False)
    monkeypatch.setattr('claim_api.sms_link.TwilioSmsConfig.from_env', lambda: object())
    routes = journey.notification_routes(NotificationConnection('+33612345678'), uuid4(), notification_review())
    preview = journey.notification_preview(routes)
    assert preview[0]['recipient'].endswith('5678') and preview[0]['recipient'] != '+33612345678'
    assert preview[1]['status'] == 'unavailable' and preview[1]['recipient'] is None
    monkeypatch.setenv('CLAIM_NOTIFICATION_MODE', 'broken')
    assert all(r['status'] == 'unavailable' for r in
               journey.notification_routes(NotificationConnection('+33612345678'), uuid4(), notification_review()))


def test_notification_preview_uses_actual_live_test_inbox_and_validates_mailer(monkeypatch):
    journey = AccidentJourney(SimpleNamespace())
    monkeypatch.setenv('CLAIM_NOTIFICATION_MODE', 'live')
    monkeypatch.setenv('CLAIM_EMAIL_TO', 'reviewer@company.com')
    monkeypatch.setenv('RESEND_API_KEY', 'test-key')
    monkeypatch.setenv('CLAIMROOM_EMAIL_FROM', 'sender@company.com')
    monkeypatch.setenv('CLAIMROOM_EMAIL_REPLY_TO', 'reply@company.com')
    monkeypatch.setattr('claim_api.sms_link.TwilioSmsConfig.from_env', lambda: object())
    routes = journey.notification_routes(NotificationConnection('+33612345678'), uuid4(), notification_review())
    assert all(r['status'] == 'ready' for r in routes)
    assert routes[1]['recipient'] == 'reviewer@company.com'
    monkeypatch.setenv('CLAIM_EMAIL_TO', 'insurer@synthetic.test')
    assert journey.notification_routes(NotificationConnection('+33612345678'), uuid4(), notification_review())[1]['status'] == 'unavailable'


def test_handler_intake_correction_invalidates_and_requeues_existing_review(journey_case):
    from claim_api.case_service import CaseService
    from claim_api.models import IntakePatch
    repo, journey, flow, actor, case_id, model, _, _, _ = journey_case
    assert flow.tick(case_id)
    original = repo.get_case(actor, case_id)
    updated = CaseService(repo).update_intake(str(actor), case_id, original.state_version,
                                            IntakePatch(insured_name='Corrected Customer'))
    assert updated.voice_session == original.voice_session
    assert updated.content_revision == original.content_revision + 1
    assert journey.view(actor, case_id)['review'] is None
    assert flow.tick(case_id)
    assert model.calls == 2
    assert journey.view(actor, case_id)['review'] is not None


def test_notification_preview_requires_mailer_api_key_before_live_approval(monkeypatch):
    journey = AccidentJourney(SimpleNamespace())
    monkeypatch.setenv('CLAIM_NOTIFICATION_MODE', 'live')
    monkeypatch.setenv('CLAIM_EMAIL_TO', 'reviewer@company.com')
    monkeypatch.setenv('RESEND_API_KEY', '')
    monkeypatch.setenv('CLAIMROOM_EMAIL_FROM', 'sender@company.com')
    monkeypatch.setenv('CLAIMROOM_EMAIL_REPLY_TO', 'reply@company.com')
    monkeypatch.setattr('claim_api.sms_link.TwilioSmsConfig.from_env', lambda: object())
    routes = journey.notification_routes(NotificationConnection('+33612345678'), uuid4(), notification_review())
    assert routes[1]['status'] == 'unavailable'
    assert routes[1]['error_code'] == 'notification_not_configured'
    assert routes[1]['http_status'] == 503
