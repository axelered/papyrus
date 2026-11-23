from pathlib import PurePath

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from server.api.output import check_shared_file, source_of
from server.api.request import op_context, openapi_body
from server.auth import require_api_key
from server.services.detect import extension_match
from server.types.inspect import InspectChecks, InspectRequest, InspectResponse
from server.utils.openapi import problem_ranges
from server.utils.timing import stage

router = APIRouter(prefix='/v1', tags=['inspect'], dependencies=[Depends(require_api_key)])


@router.post(
    '/inspect',
    summary='Detect the format of a source and check it',
    openapi_extra=openapi_body(InspectRequest),
    response_model=InspectResponse,
    responses={200: {'description': 'Source facts and checks'}, **problem_ranges()},
)
async def inspect(request: Request) -> JSONResponse:
    """Materialize and detect only, no worker; every format is inspectable (adr/inspect.md)"""
    async with op_context(request, InspectRequest) as op:
        with stage('materialize'):
            source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
        # the declared extension is read before detection renames the file to its detected one
        name = source.path.name
        with stage('detect'):
            document = await request.app.state.services.detect.detect(source.path)
        check_shared_file(source, document)
        response = InspectResponse(
            source=source_of(op.model.source, name, document, source),
            options=op.model.options,
            checks=InspectChecks(extension_match=extension_match(document.format, PurePath(name).suffix)),
        )
        return JSONResponse(response.model_dump(mode='json', exclude_none=True))
