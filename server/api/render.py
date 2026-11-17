import base64
from collections.abc import Callable
from dataclasses import replace

from fastapi import APIRouter, Depends, Request, Response

from server.api.output import image_responses, respond, source_type_of
from server.api.request import op_context, openapi_body
from server.auth import require_api_key
from server.errors import UnsupportedFormat
from server.services.render import RenderSpec
from server.types.common import FormatLabel
from server.types.convert import ConvertFormat
from server.types.render import Fit, RenderRequest, RenderResult, ThumbnailRequest

RENDER_FORMATS = frozenset(
    {
        FormatLabel.PDF,
        FormatLabel.PNG,
        FormatLabel.JPEG,
        FormatLabel.WEBP,
        FormatLabel.GIF,
        FormatLabel.BMP,
        FormatLabel.TIFF,
        FormatLabel.AVIF,
    }
)

# rendered through a one-page PDF export by LibreOffice (adr/convert.md)
PDF_CONVERSIONS = frozenset(
    {
        FormatLabel.DOCX,
        FormatLabel.PPTX,
        FormatLabel.XLSX,
        FormatLabel.DOC,
        FormatLabel.PPT,
        FormatLabel.XLS,
        FormatLabel.ODT,
        FormatLabel.ODP,
        FormatLabel.ODS,
        FormatLabel.RTF,
        FormatLabel.HTML,
    }
)

router = APIRouter(prefix='/v1', tags=['render'], dependencies=[Depends(require_api_key)])


@router.post(
    '/thumbnail',
    summary='Render a page or image into a fixed size box',
    openapi_extra=openapi_body(ThumbnailRequest),
    response_model=None,
    responses=image_responses(RenderResult),
)
async def thumbnail(request: Request) -> Response:
    def spec(model: ThumbnailRequest) -> RenderSpec:
        options = model.options
        return RenderSpec(options.page, options.width, options.height, None, options.fit, options.format)

    return await _render(request, ThumbnailRequest, spec, lambda stem, _: stem)


@router.post(
    '/render',
    summary='Render a page or image by width, height or dpi',
    openapi_extra=openapi_body(RenderRequest),
    response_model=None,
    responses=image_responses(RenderResult),
)
async def render(request: Request) -> Response:
    def spec(model: RenderRequest) -> RenderSpec:
        options = model.options
        return RenderSpec(options.page, options.width, options.height, options.dpi, Fit.CONTAIN, options.format)

    return await _render(request, RenderRequest, spec, lambda stem, page: f'{stem}-{page}')


async def _render[M: ThumbnailRequest | RenderRequest](
    request: Request,
    model: type[M],
    spec_of: Callable[[M], RenderSpec],
    name_of: Callable[[str, int], str],
) -> Response:
    """Shared coordinator: materialize, detect, route, render one page, respond"""
    services = request.app.state.services
    async with op_context(request, model) as op:
        source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
        document = await services.detect.detect(source)
        if document.format.label not in RENDER_FORMATS | PDF_CONVERSIONS:
            raise UnsupportedFormat(
                f'{document.format.mime_type} ({document.format.label}) is not supported for rendering'
            )
        spec = spec_of(op.model)
        stem = document.path.stem
        target = op.workspace.new_file(f'{name_of(stem, spec.page)}.{spec.format.extension}')
        if document.format.label in PDF_CONVERSIONS:
            # only the requested page is exported, so the page count is unknown afterwards
            pdf = op.workspace.new_file(f'{stem}.pdf')
            await services.convert.convert(document.path, pdf, ConvertFormat.PDF, page=spec.page)
            raster = await services.render.page(pdf, target, replace(spec, page=1))
            raster = replace(raster, page=spec.page, page_count=None)
        else:
            raster = await services.render.page(document.path, target, spec)
        source_type = source_type_of(document.format)

        def as_json() -> RenderResult:
            # page_count stays unset, so omitted from JSON, when the page count is unknown
            extra = {} if raster.page_count is None else {'page_count': raster.page_count}
            return RenderResult(
                data=base64.b64encode(target.read_bytes()).decode('ascii'),
                content_type=spec.format.mime_type,
                width=raster.width,
                height=raster.height,
                page=raster.page,
                source_type=source_type,
                **extra,
            )

        return await respond(request, op, target, spec.format.mime_type, as_json, source_type)
