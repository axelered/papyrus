from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server.api.admission import Admission
from server.types.common import RootResponse, StatusResponse
from server.utils.problem import problem

router = APIRouter(tags=['health'])


@router.get('/')
async def read_root() -> RootResponse:
    return RootResponse()


@router.get('/health')
async def health() -> StatusResponse:
    return StatusResponse()


@router.get('/ready', response_model=StatusResponse, responses={503: {'description': 'Admission exhausted'}})
async def ready(request: Request) -> StatusResponse | JSONResponse:
    admission: Admission = request.app.state.admission
    if admission.available <= 0:
        return problem(503, 'not_ready', f'{admission.in_flight} requests in flight')
    return StatusResponse()
