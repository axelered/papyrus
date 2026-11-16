from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from server.api.output import Receipt, SourceType, respond
from server.api.request import OpRequest, OutputMode, inline_refs, op_context, openapi_body
from server.auth import require_api_key
from server.errors import UnsupportedFormat
from server.formats import Format, FormatGroup, FormatLabel

MEDIA_TYPE = 'text/markdown; charset=utf-8'

MARKDOWN_FORMATS = frozenset(
    {
        FormatLabel.PDF,
        FormatLabel.DOCX,
        FormatLabel.PPTX,
        FormatLabel.XLSX,
        FormatLabel.XLS,
        FormatLabel.OUTLOOK,
        FormatLabel.HTML,
        FormatLabel.CSV,
        FormatLabel.TSV,
        FormatLabel.MARKDOWN,
        FormatLabel.TXT,
        FormatLabel.JSON,
        FormatLabel.JSONL,
        FormatLabel.XML,
        FormatLabel.RSS,
        FormatLabel.ATOM,
        FormatLabel.IPYNB,
        FormatLabel.EPUB,
        FormatLabel.ZIP,
    }
)

router = APIRouter(prefix='/v1', tags=['markdown'], dependencies=[Depends(require_api_key)])


class MarkdownRequest(OpRequest):
    output: OutputMode = OutputMode.JSON


class MarkdownResult(BaseModel):
    markdown: str
    title: str | None
    source_type: SourceType


def accepts(value: Format) -> bool:
    """Explicit allowlist plus every format derived from magika that is text (code, configs, markup)"""
    return value.label in MARKDOWN_FORMATS or (value.derived and value.text)


def is_code(value: Format) -> bool:
    """Only derived code is fenced; explicit formats grouped as code (ipynb) have a dedicated markitdown converter"""
    return value.derived and value.group == FormatGroup.CODE


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
                    'schema': {
                        'anyOf': [
                            inline_refs(MarkdownResult.model_json_schema()),
                            inline_refs(Receipt.model_json_schema()),
                        ]
                    }
                },
                'text/markdown': {'schema': {'type': 'string'}},
            },
        }
    },
)
async def markdown(request: Request) -> Response:
    services = request.app.state.services
    async with op_context(request, MarkdownRequest) as op:
        source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
        document = await services.detect.detect(source)
        if not accepts(document.format):
            raise UnsupportedFormat(
                f'{document.format.mime_type} ({document.format.label}) is not supported for markdown extraction'
            )
        target = op.workspace.new_file(f'{document.path.stem}.md')
        if is_code(document.format):
            title = None
            await services.markdown.fence_code(document.path, target, document.format.label)
        else:
            title = await services.markdown.convert(document.path, target)
        source_type = SourceType.of(document.format)
        return await respond(
            request,
            op,
            target,
            MEDIA_TYPE,
            lambda: MarkdownResult(markdown=target.read_text(encoding='utf-8'), title=title, source_type=source_type),
            source_type,
        )
