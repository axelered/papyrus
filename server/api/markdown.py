from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from server.api.output import op_responses, respond, result_of, source_of
from server.api.request import op_context, openapi_body
from server.auth import require_api_key
from server.errors import (
    ImageTooLarge,
    OcrFailed,
    OcrNotConfigured,
    OcrRateLimited,
    TooManyPages,
    UnsupportedFormat,
)
from server.io.workspace import Workspace
from server.services.detect import Document, Format
from server.services.markdown import CHUNKS
from server.types.common import Encoding, FormatGroup, FormatLabel
from server.types.convert import ConvertFormat
from server.types.markdown import Extracted, MarkdownOptions, MarkdownRequest, MarkdownResponse, ReadMode
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
    responses=op_responses(
        {'text/markdown': {'schema': {'type': 'string'}}},
        UnsupportedFormat,
        ImageTooLarge,
        TooManyPages,
        OcrNotConfigured,
        OcrFailed,
        OcrRateLimited,
    ),
)
async def markdown(request: Request) -> Response:
    services = request.app.state.services
    async with op_context(request, MarkdownRequest) as op:
        options = op.model.options
        if options.read_mode == ReadMode.OCR and not services.ocr.configured:
            # rejects the request before any work, whatever the format
            raise OcrNotConfigured('readMode ocr needs an OCR backend, this server has none')
        with stage('materialize'):
            source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
        name = source.path.name
        with stage('detect'):
            document = await services.detect.detect(source.path)
        if options.read_mode == ReadMode.OCR and not services.ocr.accepts(document.format):
            options = options.model_copy(update={'read_mode': ReadMode.TEXT})
        target = op.workspace.new_file(f'{document.path.stem}.md')
        chunks_target = op.workspace.new_file(f'{document.path.stem}.chunks.json') if options.chunking else None
        if options.read_mode == ReadMode.OCR:
            result = await services.ocr.read(document, op.workspace, target, options.chunking, chunks_target)
        else:
            title = await _extract_text(services, op.workspace, document, target, options, chunks_target)
            result = Extracted(title=title)

        response = MarkdownResponse(
            source=source_of(op.model.source, name, document, source, **asdict(result)),
            options=options,
            result=result_of(target, FormatLabel.MARKDOWN),
            chunks=CHUNKS.validate_json(chunks_target.read_bytes()) if chunks_target else None,
        )
        return await respond(request, op, response, target, Encoding.UTF8)


async def _extract_text(
    services: Any,
    workspace: Workspace,
    document: Document,
    target: Path,
    options: MarkdownOptions,
    chunks_target: Path | None,
) -> str | None:
    """Writes the text stored in the file to `target`, returns the document title"""
    label = document.format.label
    if not accepts(document.format):
        hint = ', set readMode to ocr to read images' if document.format.group == FormatGroup.IMAGE else ''
        raise UnsupportedFormat(f'{document.format.mime_type} ({label}) is not supported for markdown extraction{hint}')
    source = document.path
    if conversion := CONVERSIONS.get(label):
        source = workspace.new_file(f'{document.path.stem}.{conversion}')
        with stage('convert'):
            await services.convert.convert(document.path, source, conversion)
    with stage('markdown'):
        if is_code(document.format):
            await services.markdown.fence_code(source, target, label, options.chunking, chunks_target)
            return None
        return await services.markdown.convert(source, target, options.chunking, chunks_target)
