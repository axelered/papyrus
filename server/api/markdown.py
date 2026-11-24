import asyncio
from collections.abc import Awaitable, Callable
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
    PageOutOfRange,
    TooManyPages,
    UnsupportedFormat,
)
from server.io.workspace import Workspace
from server.services.detect import Document, Format
from server.services.markdown import CHUNKS
from server.services.render import Raster, RenderSpec
from server.types.common import Encoding, FormatGroup, FormatLabel, ImageFormat
from server.types.convert import ConvertFormat
from server.types.markdown import MarkdownOptions, MarkdownRequest, MarkdownResponse, ReadMode
from server.types.render import Fit
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

# formats with pages that readMode ocr renders through LibreOffice; spreadsheets are left out, pagination cuts them
OCR_CONVERSIONS = frozenset(
    {
        FormatLabel.DOCX,
        FormatLabel.DOC,
        FormatLabel.ODT,
        FormatLabel.RTF,
        FormatLabel.PPTX,
        FormatLabel.PPT,
        FormatLabel.ODP,
    }
)
OCR_IMAGES = frozenset(
    {
        FormatLabel.PNG,
        FormatLabel.JPEG,
        FormatLabel.WEBP,
        FormatLabel.GIF,
        FormatLabel.BMP,
        FormatLabel.TIFF,
        FormatLabel.AVIF,
    }
)
OCR_FORMATS = OCR_CONVERSIONS | OCR_IMAGES | {FormatLabel.PDF}
# the frames of other images are an animation, not pages
OCR_SINGLE_PAGE = OCR_IMAGES - {FormatLabel.TIFF}

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
        if options.read_mode == ReadMode.OCR:
            # rejects the request before any work, whatever the format
            _ = services.ocr.config
        with stage('materialize'):
            source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
        name = source.path.name
        with stage('detect'):
            document = await services.detect.detect(source.path)
        if options.read_mode == ReadMode.OCR and document.format.label not in OCR_FORMATS:
            options = options.model_copy(update={'read_mode': ReadMode.TEXT})
        target = op.workspace.new_file(f'{document.path.stem}.md')
        chunks_target = op.workspace.new_file(f'{document.path.stem}.chunks.json') if options.chunking else None
        if options.read_mode == ReadMode.OCR:
            facts = await _read_pages(services, op.workspace, document, target)
            if options.chunking and chunks_target:
                with stage('chunk'):
                    await services.markdown.chunk(target, chunks_target, options.chunking)
        else:
            facts = {'title': await _extract_text(services, op.workspace, document, target, options, chunks_target)}

        response = MarkdownResponse(
            source=source_of(op.model.source, name, document, source, **facts),
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
        hint = ', set readMode to ocr to read images' if label in OCR_IMAGES else ''
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


async def _read_pages(services: Any, workspace: Workspace, document: Document, target: Path) -> dict[str, Any]:
    """Writes the pages read by the OCR backend to `target`, returns the source facts (adr/ocr.md, Scheduling)"""
    ocr = services.ocr
    label = document.format.label
    source = document.path
    if label in OCR_CONVERSIONS:
        source = workspace.new_file(f'{document.path.stem}.pdf')
        with stage('convert'):
            await services.convert.convert(document.path, source, ConvertFormat.PDF)

    async def render(number: int) -> tuple[Path, Raster]:
        spec = RenderSpec(
            page=number,
            width=None,
            height=None,
            dpi=ocr.adapter.dpi,
            fit=Fit.CONTAIN,
            format=ImageFormat.JPEG,
            max_pixels=ocr.adapter.max_pixels,
        )
        image = workspace.new_file(f'page-{number}.jpg')
        async with ocr.rendering():
            return image, await services.render.page(source, image, spec)

    with stage('render'):
        try:
            first, raster = await render(1)
        except PageOutOfRange:
            raise UnsupportedFormat('the document has no pages') from None
    count = 1 if label in OCR_SINGLE_PAGE else raster.page_count or 1
    if count > ocr.config.max_pages:
        raise TooManyPages(f'the document has {count} pages, at most {ocr.config.max_pages} can be read')

    async def read(number: int) -> str:
        images = [first] if number == 1 else []

        async def page() -> Path:
            if not images:
                images.append((await render(number))[0])
            return images[0]

        try:
            return await ocr.read(number, page)
        finally:
            for image in images:
                image.unlink(missing_ok=True)

    with stage('ocr'):
        pages = await _run_pages(read, count, ocr.config.concurrency)
    unit = 'Slide' if document.format.group == FormatGroup.PRESENTATION else 'Page'
    markdown = '\n\n'.join(
        f'<!-- {unit} number: {number} -->\n\n{text}'.rstrip() for number, text in enumerate(pages, start=1)
    )
    target.write_text(markdown, encoding='utf-8', newline='')
    width, height = raster.source_size or (None, None)
    return {'page_count': raster.page_count, 'width': width, 'height': height}


async def _run_pages(read: Callable[[int], Awaitable[str]], count: int, concurrency: int) -> list[str]:
    """Pages in order; at most `concurrency` are queued, so a long document does not take every place in the FIFO"""
    pages = [''] * count
    pending = iter(range(1, count + 1))

    async def work() -> None:
        for number in pending:
            pages[number - 1] = await read(number)

    try:
        async with asyncio.TaskGroup() as group:
            for _ in range(min(concurrency, count)):
                group.create_task(work())
    except ExceptionGroup as e:
        # problem handlers only know single errors
        raise e.exceptions[0] from None
    return pages
