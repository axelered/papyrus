from fastapi import APIRouter, Depends, Request, Response

from server.api.output import op_responses, respond, result_of, source_of
from server.api.request import op_context, openapi_body
from server.auth import require_api_key
from server.errors import UnsupportedFormat
from server.services.detect import Format
from server.services.markdown import CHUNKS
from server.types.common import Encoding, FormatGroup, FormatLabel
from server.types.convert import ConvertFormat
from server.types.markdown import MarkdownRequest, MarkdownResponse
from server.utils.timing import stage

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

# formats markitdown cannot read go through LibreOffice first; OOXML keeps headings and tables for chunking
CONVERSIONS: dict[FormatLabel, ConvertFormat] = {
    FormatLabel.DOC: ConvertFormat.DOCX,
    FormatLabel.RTF: ConvertFormat.DOCX,
    FormatLabel.ODT: ConvertFormat.DOCX,
    FormatLabel.PPT: ConvertFormat.PPTX,
    FormatLabel.ODP: ConvertFormat.PPTX,
    FormatLabel.ODS: ConvertFormat.XLSX,
}

router = APIRouter(prefix='/v1', tags=['markdown'], dependencies=[Depends(require_api_key)])


def accepts(value: Format) -> bool:
    """Explicit allowlist plus every format derived from magika that is text (code, configs, markup)"""
    return value.label in MARKDOWN_FORMATS or value.label in CONVERSIONS or (value.derived and value.text)


def is_code(value: Format) -> bool:
    """Only derived code is fenced; explicit formats grouped as code (ipynb) have a dedicated markitdown converter"""
    return value.derived and value.group == FormatGroup.CODE


@router.post(
    '/markdown',
    summary='Extract markdown from a document',
    openapi_extra=openapi_body(MarkdownRequest),
    response_model=MarkdownResponse,
    # chunked file output is the JSON document, documented by the response model
    responses=op_responses({'text/markdown': {'schema': {'type': 'string'}}}, UnsupportedFormat),
)
async def markdown(request: Request) -> Response:
    services = request.app.state.services
    async with op_context(request, MarkdownRequest) as op:
        with stage('materialize'):
            source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
        name = source.path.name
        with stage('detect'):
            document = await services.detect.detect(source.path)
        if not accepts(document.format):
            raise UnsupportedFormat(
                f'{document.format.mime_type} ({document.format.label}) is not supported for markdown extraction'
            )
        stem = document.path.stem
        source_path = document.path
        if conversion := CONVERSIONS.get(document.format.label):
            source_path = op.workspace.new_file(f'{stem}.{conversion}')
            with stage('convert'):
                await services.convert.convert(document.path, source_path, conversion)
        target = op.workspace.new_file(f'{stem}.md')
        chunking = op.model.options.chunking
        chunks_target = op.workspace.new_file(f'{stem}.chunks.json') if chunking else None
        with stage('markdown'):
            if is_code(document.format):
                title = None
                await services.markdown.fence_code(
                    document.path, target, document.format.label, chunking, chunks_target
                )
            else:
                title = await services.markdown.convert(source_path, target, chunking, chunks_target)

        response = MarkdownResponse(
            source=source_of(op.model.source, name, document, source, title=title),
            options=op.model.options,
            result=result_of(target, FormatLabel.MARKDOWN),
            chunks=CHUNKS.validate_json(chunks_target.read_bytes()) if chunks_target else None,
        )
        return await respond(request, op, response, target, Encoding.UTF8)
