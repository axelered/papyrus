import base64
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response

from server.api.output import image_responses, respond
from server.api.request import op_context, openapi_body
from server.auth import require_api_key
from server.types.screenshot import ScreenshotRequest, ScreenshotResult

router = APIRouter(prefix='/v1', tags=['screenshot'], dependencies=[Depends(require_api_key)])


@router.post(
    '/screenshot',
    summary='Screenshot a web page in headless Chromium',
    openapi_extra=openapi_body(ScreenshotRequest),
    response_model=None,
    responses=image_responses(ScreenshotResult),
)
async def screenshot(request: Request) -> Response:
    async with op_context(request, ScreenshotRequest) as op:
        url = op.model.source
        options = op.model.options
        target = op.workspace.new_file(f'{urlsplit(url).hostname or "screenshot"}.{options.format.extension}')
        capture = await request.app.state.services.browser.screenshot(url, target, options)

        def as_json() -> ScreenshotResult:
            return ScreenshotResult(
                data=base64.b64encode(target.read_bytes()).decode('ascii'),
                content_type=options.format.mime_type,
                width=capture.width,
                height=capture.height,
                url=capture.url,
                status=capture.status,
            )

        return await respond(request, op, target, options.format.mime_type, as_json)
