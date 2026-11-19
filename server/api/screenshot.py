from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response

from server.api.output import image_responses, respond, result_of
from server.api.request import op_context, openapi_body
from server.auth import require_api_key
from server.types.common import Encoding, Fetch, Source
from server.types.screenshot import ScreenshotRequest, ScreenshotResponse
from server.utils.timing import stage

router = APIRouter(prefix='/v1', tags=['screenshot'], dependencies=[Depends(require_api_key)])


@router.post(
    '/screenshot',
    summary='Screenshot a web page in headless Chromium',
    openapi_extra=openapi_body(ScreenshotRequest),
    response_model=None,
    responses=image_responses(ScreenshotResponse),
)
async def screenshot(request: Request) -> Response:
    async with op_context(request, ScreenshotRequest) as op:
        url = op.model.source
        options = op.model.options
        target = op.workspace.new_file(f'{urlsplit(url).hostname or "screenshot"}.{options.format.extension}')
        with stage('screenshot'):
            capture = await request.app.state.services.browser.screenshot(url, target, options)

        # a navigated page has no file: the source carries only its URI and what the navigation reported
        response = ScreenshotResponse(
            source=Source(uri=url, fetch=Fetch(url=capture.url, status=capture.status)),
            options=options,
            result=result_of(target, options.format, width=capture.width, height=capture.height),
        )
        return await respond(request, op, response, target, Encoding.BASE64)
