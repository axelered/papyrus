from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server import version
from server.api.admission import Admission
from server.errors import NotReady
from server.types.common import HelloResponse, StatusResponse
from server.utils.openapi import problem_responses
from server.utils.problem import problem

router = APIRouter(tags=['health'])


@router.get('/', summary='Server name and version')
async def read_root() -> HelloResponse:
    return HelloResponse(version=version.__version__, watermark=version.__watermark__)


@router.get('/health', summary='Liveness probe')
async def health() -> StatusResponse:
    return StatusResponse()


@router.get('/ready', summary='Readiness probe', response_model=StatusResponse, responses=problem_responses(NotReady))
async def ready(request: Request) -> StatusResponse | JSONResponse:
    admission: Admission = request.app.state.admission
    if admission.available <= 0:
        # rendered directly: raising would log a warning on every probe of a busy replica
        return problem(NotReady.status, NotReady.code, f'{admission.in_flight} requests in flight')
    return StatusResponse()
