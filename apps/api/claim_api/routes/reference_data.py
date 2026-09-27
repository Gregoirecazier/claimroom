"""Authenticated read access to the demo registry and simulated camera archive."""
import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from claim_api.analysis import AnalysisError
from claim_api.auth import CurrentUser
from claim_api.demo_media import MEDIA_ROOT
from claim_api.postgres_cases import PostgresCaseRepository
from claim_api.reference_data import ReferenceData
from claim_api.routes.media_workflow import translate

router = APIRouter(prefix='/v1/reference-data',tags=['demo reference data'])


def reference_data():
    url = os.getenv('DATABASE_URL','')
    if not url: raise HTTPException(503,'Référentiels indisponibles.')
    return ReferenceData(PostgresCaseRepository(url))


Service = Annotated[ReferenceData,Depends(reference_data)]


@router.get('/vehicles')
def vehicles(user: CurrentUser, service: Service,
             plate: Annotated[str | None,Query(min_length=1,max_length=20)] = None,
             country: Annotated[str | None,Query(pattern='^(FR|UK|GB)$')] = None,
             limit: Annotated[int,Query(ge=1,le=200)] = 50,
             offset: Annotated[int,Query(ge=0)] = 0):
    try: return service.vehicles(plate=plate,country=country,limit=limit,offset=offset)
    except Exception as error: raise translate(error) from error


@router.get('/videos')
def videos(user: CurrentUser, service: Service):
    try: return service.videos()
    except Exception as error: raise translate(error) from error


@router.get('/videos/{video_id}/content')
def content(video_id: UUID,user: CurrentUser,service: Service):
    try: entries = service.video_catalogue(video_id)
    except AnalysisError as error: raise HTTPException(503,str(error)) from error
    except Exception as error: raise translate(error) from error
    if not entries: raise HTTPException(404,'Vidéo absente du catalogue.')
    return FileResponse(MEDIA_ROOT / entries[0]['path'],media_type='video/mp4',
                        headers={'Cache-Control':'private, no-store'})
