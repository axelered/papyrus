import base64
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Request, Response

from server.api.output import image_responses, respond, source_type_of
from server.api.request import Op, op_context, openapi_body
from server.auth import require_api_key
from server.errors import PageOutOfRange, UnsupportedFormat
from server.io.uri import HTTP_SCHEMES
from server.services.browser import BrowserService
from server.services.convert import ConvertService
from server.services.detect import Document
from server.services.render import Raster, RenderService, RenderSpec
from server.types.common import FormatLabel
from server.types.convert import ConvertFormat
from server.types.render import Fit, RenderRequest, RenderResult, ThumbnailRequest
from server.types.screenshot import ScreenshotOptions
from server.utils.timing import stage

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

# HTML thumbnails show the page as on screen in a desktop browser, the viewport takes the box shape (adr/render.md)
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHTS = (400, 2160)
# reserved name that never resolves, so relative references of uploaded HTML fail at the proxy
DOCUMENT_ORIGIN = 'http://document.invalid'

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

    return await _render(request, ThumbnailRequest, spec, lambda stem, _: stem, html_screenshot=True)


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
    html_screenshot: bool = False,
) -> Response:
    """Shared coordinator: materialize, detect, route, render one page, respond

    `html_screenshot` renders HTML as a browser screenshot instead of LibreOffice pages (adr/render.md).
    """
    services = request.app.state.services
    async with op_context(request, model) as op:
        with stage('materialize'):
            source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
        with stage('detect'):
            document = await services.detect.detect(source.path)
        if document.format.label not in RENDER_FORMATS | PDF_CONVERSIONS:
            raise UnsupportedFormat(
                f'{document.format.mime_type} ({document.format.label}) is not supported for rendering'
            )
        spec = spec_of(op.model)
        target = op.workspace.new_file(f'{name_of(document.path.stem, spec.page)}.{spec.format.extension}')
        if html_screenshot and document.format.label == FormatLabel.HTML:
            raster = await _screenshot_html(services.browser, services.render, op, document, target, spec)
        elif document.format.label in PDF_CONVERSIONS:
            raster = await _convert_page(services.convert, services.render, op, document, target, spec)
        else:
            with stage('render'):
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


def thumbnail_viewport(spec: RenderSpec) -> ScreenshotOptions:
    """Desktop width, height from the box aspect: the capture fills the box without cropping the sides"""
    width, height = spec.width or VIEWPORT_WIDTH, spec.height or VIEWPORT_WIDTH
    low, high = VIEWPORT_HEIGHTS
    return ScreenshotOptions(width=VIEWPORT_WIDTH, height=min(high, max(low, round(VIEWPORT_WIDTH * height / width))))


async def _convert_page(
    convert: ConvertService, render: RenderService, op: Op, document: Document, target: Path, spec: RenderSpec
) -> Raster:
    """Office and HTML through a one-page PDF export; the page count is unknown afterwards"""
    pdf = op.workspace.new_file(f'{document.path.stem}.pdf')
    with stage('convert'):
        await convert.convert(document.path, pdf, ConvertFormat.PDF, page=spec.page)
    with stage('render'):
        raster = await render.page(pdf, target, replace(spec, page=1))
    return replace(raster, page=spec.page, page_count=None)


async def _screenshot_html(
    browser: BrowserService, render: RenderService, op: Op, document: Document, target: Path, spec: RenderSpec
) -> Raster:
    """The page as it looks on screen; the PNG then takes the image path of the render worker"""
    if spec.page > 1:
        raise PageOutOfRange(f'page {spec.page} is out of range, the document has 1')
    source = op.model.source
    origin = source if urlsplit(source).scheme in HTTP_SCHEMES else f'{DOCUMENT_ORIGIN}/{quote(document.path.name)}'
    shot = op.workspace.new_file(f'{document.path.stem}.png')
    with stage('screenshot'):
        await browser.screenshot(origin, shot, thumbnail_viewport(spec), document=document.path)
    with stage('render'):
        raster = await render.page(shot, target, spec)
    return replace(raster, page_count=1)
