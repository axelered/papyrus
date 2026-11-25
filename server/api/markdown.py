from dataclasses import asdict
from typing import TYPE_CHECKING

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
from server.services.markdown import CHUNKS
from server.types.common import Encoding, FormatGroup, FormatLabel
from server.types.markdown import MarkdownRequest, MarkdownResponse, ReadMode
from server.utils.timing import stage

if TYPE_CHECKING:
    from server.factory import Services

router = APIRouter(prefix='/v1', tags=['markdown'], dependencies=[Depends(require_api_key)])


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
    services: Services = request.app.state.services
    async with op_context(request, MarkdownRequest) as op:
        options = op.model.options
        with stage('materialize'):
            source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
        name = source.path.name
        with stage('detect'):
            original = await services.detect.detect(source.path)
        # todo: for mode=OCR modernize can be skipped, office files are converted to PDF anyway
        with stage('modernize'):
            document = await services.convert.modernize(original, op.workspace)

        mode = options.read_mode
        if mode == ReadMode.TEXT:
            if not services.markdown.accepts(document.format):
                raise UnsupportedFormat(
                    f'{original.format.mime_type} ({original.format.label}) is not supported for '
                    f'markdown extraction'
                    + (', set readMode to ocr to read images' if original.format.group == FormatGroup.IMAGE else '')
                )
        elif mode == ReadMode.OCR:
            if not services.ocr.configured:
                raise OcrNotConfigured('readMode ocr needs an OCR backend, this server has none')
            if not services.ocr.accepts(document.format):
                mode = ReadMode.TEXT  # fallback: formats without pages are read as text

        target = op.workspace.new_file(f'{document.path.stem}.md')
        chunks_target = op.workspace.new_file(f'{document.path.stem}.chunks.json') if options.chunking else None
        if mode == ReadMode.OCR:
            with stage('ocr'):
                result = await services.ocr.read(document, op.workspace, target, options.chunking, chunks_target)
        else:
            with stage('markdown'):
                result = await services.markdown.read(document, target, options.chunking, chunks_target)

        response = MarkdownResponse(
            source=source_of(op.model.source, name, original, source, **asdict(result)),
            options=options.model_copy(update={'read_mode': mode}),
            result=result_of(target, FormatLabel.MARKDOWN),
            chunks=CHUNKS.validate_json(chunks_target.read_bytes()) if chunks_target else None,
        )
        return await respond(request, op, response, target, Encoding.UTF8)
