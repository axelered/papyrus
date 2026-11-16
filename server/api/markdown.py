from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from server.api.output import Receipt, respond
from server.api.request import OpRequest, OutputMode, op_context, openapi_body
from server.auth import require_api_key

MEDIA_TYPE = 'text/markdown; charset=utf-8'

router = APIRouter(prefix='/v1', tags=['markdown'], dependencies=[Depends(require_api_key)])


class MarkdownRequest(OpRequest):
    output: OutputMode = OutputMode.JSON


class MarkdownResult(BaseModel):
    markdown: str
    title: str | None


@router.post(
    '/markdown',
    summary='Extract markdown from a document',
    openapi_extra=openapi_body(MarkdownRequest),
    response_model=None,
    responses={
        200: {
            'description': 'Markdown as JSON, as a file, or a receipt when `destination` is set',
            'content': {
                'application/json': {
                    'schema': {'anyOf': [MarkdownResult.model_json_schema(), Receipt.model_json_schema()]}
                },
                'text/markdown': {'schema': {'type': 'string'}},
            },
        }
    },
)
async def markdown(request: Request) -> Response:
    async with op_context(request, MarkdownRequest) as op:
        source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
        target = op.workspace.new_file(f'{source.stem}.md')
        title = await request.app.state.services.markdown.convert(source, target)
        return await respond(
            request,
            op,
            target,
            MEDIA_TYPE,
            lambda: MarkdownResult(markdown=target.read_text(encoding='utf-8'), title=title),
        )
