from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from server.errors import ImageTooLarge, PageOutOfRange, UnsupportedFormat
from server.services.render import Plan, RenderService, RenderSpec, plan
from server.types.common import MAX_IMAGE_SIDE, ImageFormat
from server.types.render import Fit
from test.fixtures.documents import DocumentFactory
from test.fixtures.samples import image, pdf_pages, png_header

pytestmark = pytest.mark.anyio

A4 = (595, 842)


def _spec(
    width: int | None = None,
    height: int | None = None,
    dpi: int | None = None,
    fit: Fit = Fit.CONTAIN,
    page: int = 1,
    format: ImageFormat = ImageFormat.PNG,
) -> RenderSpec:
    return RenderSpec(page=page, width=width, height=height, dpi=dpi, fit=fit, format=format)


# - - - Sizing plan


@pytest.mark.parametrize(
    ('source', 'spec', 'vector', 'size', 'crop'),
    [
        (A4, _spec(256, 256), True, (181, 256), None),
        (A4, _spec(256, 256, fit=Fit.COVER), True, (256, 362), (256, 256)),
        (A4, _spec(), True, (1240, 1754), None),
        (A4, _spec(dpi=72), True, A4, None),
        (A4, _spec(width=800), True, (800, 1132), None),
        (A4, _spec(height=421), True, (298, 421), None),
        ((2384, 3370), _spec(dpi=600), True, (2898, MAX_IMAGE_SIDE), None),
        ((100, 50), _spec(256, 256), False, (100, 50), None),
        ((100, 50), _spec(256, 256, fit=Fit.COVER), False, (100, 50), (50, 50)),
        ((640, 480), _spec(dpi=300), False, (640, 480), None),
        ((640, 480), _spec(), False, (640, 480), None),
        ((5000, 100), _spec(), False, (MAX_IMAGE_SIDE, 82), None),
        ((1000, 500), _spec(width=500), False, (500, 250), None),
        # pathological aspect: the intermediate cover bitmap is clamped, so the crop ends up smaller than the box
        ((100, 10_000), _spec(256, 256, fit=Fit.COVER), True, (41, MAX_IMAGE_SIDE), (41, 41)),
    ],
)
def test_plan(source: tuple[int, int], spec: RenderSpec, vector: bool, size: tuple[int, int], crop):
    layout = plan(*source, spec, vector=vector)

    assert (layout.size, layout.crop) == (size, crop)


def test_plan_keeps_scale_for_pdfium():
    assert plan(*A4, _spec(dpi=144), vector=True) == Plan(2.0, (1190, 1684), None)


# - - - Worker


@pytest.fixture(scope='module')
def service() -> Iterator[RenderService]:
    service = RenderService(processes=1)
    yield service
    service.close()


async def _render(service: RenderService, source: Path, tmp_path: Path, spec: RenderSpec) -> tuple[Image.Image, object]:
    target = tmp_path / f'out.{spec.format.extension}'
    raster = await service.page(source, target, spec)
    output = Image.open(target)
    assert output.size == (raster.width, raster.height)
    return output, raster


async def test_renders_selected_pdf_page(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    source = make_document('doc.pdf', pdf_pages(A4, (842, 595)))

    output, raster = await _render(service, source, tmp_path, _spec(dpi=72, page=2))

    assert output.size == (842, 595)
    assert (raster.page, raster.page_count) == (2, 2)
    assert output.getpixel((10, 10)) == (255, 255, 255)


async def test_pdf_rotation_is_applied(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    source = make_document('doc.pdf', pdf_pages((200, 100), rotate=90))

    output, _ = await _render(service, source, tmp_path, _spec(dpi=72))

    assert output.size == (100, 200)


async def test_pdf_cover_thumbnail(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    source = make_document('doc.pdf', pdf_pages(A4))

    output, _ = await _render(service, source, tmp_path, _spec(256, 256, fit=Fit.COVER, format=ImageFormat.WEBP))

    assert (output.format, output.size) == ('WEBP', (256, 256))


async def test_pdf_page_out_of_range(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    with pytest.raises(PageOutOfRange, match='has 1'):
        await service.page(make_document('doc.pdf', pdf_pages(A4)), tmp_path / 'o.png', _spec(page=2))


async def test_corrupt_pdf_is_unsupported(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    with pytest.raises(UnsupportedFormat):
        await service.page(make_document('doc.pdf', b'%PDF-1.7 broken'), tmp_path / 'o.png', _spec())


@pytest.mark.parametrize(('format', 'mode'), [(ImageFormat.PNG, 'RGBA'), (ImageFormat.JPEG, 'RGB')])
async def test_alpha_is_kept_or_flattened(
    service: RenderService, make_document: DocumentFactory, tmp_path: Path, format: ImageFormat, mode: str
):
    source = make_document('a.png', image('PNG', mode='RGBA'))

    output, _ = await _render(service, source, tmp_path, _spec(format=format))

    assert output.mode == mode


async def test_cmyk_jpeg_becomes_rgb(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    source = make_document('c.jpg', image('JPEG', mode='CMYK'))

    output, _ = await _render(service, source, tmp_path, _spec())

    assert output.mode == 'RGB'


async def test_exif_orientation_is_applied(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    exif = Image.Exif()
    exif[0x0112] = 6
    source = make_document('phone.jpg', image('JPEG', size=(60, 40), exif=exif.tobytes()))

    output, _ = await _render(service, source, tmp_path, _spec())

    assert output.size == (40, 60)


async def test_page_selects_frame(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    source = make_document('scan.tiff', image('TIFF', frames=2))

    output, raster = await _render(service, source, tmp_path, _spec(page=2))

    assert output.convert('RGB').getpixel((5, 5)) == (30, 200, 30)
    assert raster.page_count == 2
    with pytest.raises(PageOutOfRange, match='has 2'):
        await service.page(source, tmp_path / 'o.png', _spec(page=3))


async def test_images_are_not_upscaled(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    source = make_document('small.png', image('PNG', size=(64, 48)))

    output, _ = await _render(service, source, tmp_path, _spec(1024, 1024))

    assert output.size == (64, 48)


async def test_large_jpeg_is_downscaled(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    source = make_document('big.jpg', image('JPEG', size=(2000, 1000)))

    output, _ = await _render(service, source, tmp_path, _spec(256, 256))

    assert output.size == (256, 128)


async def test_decodes_avif(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    source = make_document('photo.avif', image('AVIF', size=(80, 60)))

    output, _ = await _render(service, source, tmp_path, _spec())

    assert output.size == (80, 60)


async def test_bomb_guard(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    with pytest.raises(ImageTooLarge):
        await service.page(make_document('bomb.png', png_header(30_000, 30_000)), tmp_path / 'o.png', _spec())


async def test_corrupt_image_is_unsupported(service: RenderService, make_document: DocumentFactory, tmp_path: Path):
    with pytest.raises(UnsupportedFormat):
        await service.page(make_document('x.png', b'\x89PNG broken'), tmp_path / 'o.png', _spec())
