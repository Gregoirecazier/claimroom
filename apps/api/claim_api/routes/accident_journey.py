import os
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from claim_api.auth import CurrentUser
from claim_api.accident_journey import AccidentJourney, ApproveAccident, EditAccidentEstimate, SendAccidentNotifications, photo_video_match
from claim_api.analysis import AnalysisError
from claim_api.demo_media import MEDIA_ROOT
from claim_api.media_workflow import MediaWorkflowRepository
from claim_api.routes.media_workflow import translate
from claim_api.sms_service import SmsError

router = APIRouter(prefix='/v1/cases/{case_id}/journey', tags=['automatic claim journey'])


def journey():
    url = os.getenv('DATABASE_URL','')
    if not url: raise HTTPException(503,'Dossier indisponible.')
    return AccidentJourney(MediaWorkflowRepository(url))


Service = Annotated[AccidentJourney, Depends(journey)]


@router.get('')
def view(case_id: UUID, user: CurrentUser, service: Service):
    try: return service.view(UUID(user.id),case_id)
    except Exception as error: raise translate(error) from error


@router.post('/approve')
def approve(case_id: UUID, request: ApproveAccident, user: CurrentUser, service: Service):
    try: return service.approve(UUID(user.id),case_id,request)
    except SmsError as error:
        raise HTTPException(error.status_code,detail={'code':error.code,'message':str(error),'details':{}}) from error
    except Exception as error: raise translate(error) from error


@router.post('/send')
def send(case_id: UUID, request: SendAccidentNotifications, user: CurrentUser, service: Service):
    try: return service.send_notifications(UUID(user.id), case_id, request)
    except SmsError as error:
        raise HTTPException(error.status_code, detail={'code':error.code,'message':str(error),'details':{}}) from error
    except Exception as error: raise translate(error) from error


@router.put('/estimate')
def edit_estimate(case_id: UUID, request: EditAccidentEstimate, user: CurrentUser, service: Service):
    try: return service.edit_estimate(UUID(user.id), case_id, request)
    except SmsError as error:
        raise HTTPException(error.status_code, detail={'code':error.code,'message':str(error),'details':{}}) from error
    except Exception as error: raise translate(error) from error


@router.get('/video/{video_id}')
def video(case_id: UUID, video_id: UUID, user: CurrentUser, service: Service):
    try: result = service.view(UUID(user.id),case_id)
    except Exception as error: raise translate(error) from error
    if not result['review'] or result['review']['assessment']['selected_video_id'] != str(video_id):
        raise HTTPException(404,'Vidéo absente de la proposition.')
    try: entries = service.reference_data.video_catalogue(video_id)
    except AnalysisError as error: raise HTTPException(503,str(error)) from error
    except Exception as error: raise translate(error) from error
    if not entries: raise HTTPException(404,'Vidéo indisponible.')
    return FileResponse(MEDIA_ROOT / entries[0]['path'],media_type='video/mp4',headers={'Cache-Control':'private, no-store'})


@router.get('/photos/{photo_id}/video-match')
def photo_match(case_id: UUID, photo_id: UUID, user: CurrentUser, service: Service):
    try:
        actor = UUID(user.id)
        case = service.repository.get_case(actor, case_id)
        if case is None:
            raise HTTPException(404, 'Dossier absent.')
        review = service.view(actor, case_id)['review']
        return photo_video_match(case, review, photo_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    except HTTPException:
        raise
    except Exception as error:
        raise translate(error) from error
