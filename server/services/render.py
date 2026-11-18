import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from server.errors import ImageTooLarge, PageOutOfRange, ProcessingFailed, UnsupportedFormat
from server.services.base import Service, pool_errors
from server.types.common import MAX_IMAGE_SIDE, ImageFormat
from server.types.render import Fit
from server.utils.pool import ProcessPool
from server.utils.problem import ProblemError

if TYPE_CHECKING:
    from PIL.Image import Image

TIMEOUT = 60
MAX_TASKS = 200
DEFAULT_DPI = 150
QUALITY = 85
_POINTS_PER_INCH = 72
_EPSILON = 1e-9
# exif orientations that swap width and height
_TRANSPOSED = frozenset({5, 6, 7, 8})
_ORIENTATION_TAG = 0x0112


@dataclass(frozen=True)
class RenderSpec:
    """Requested sizing intent; the worker turns it into pixels once the page size is known"""

    page: int
    width: int | None
    height: int | None
    dpi: int | None
    fit: Fit
    format: ImageFormat


@dataclass(frozen=True)
class Raster:
    width: int
    height: int
    page: int
    page_count: int | None


@dataclass(frozen=True)
class Plan:
    scale: float
    size: tuple[int, int]
    crop: tuple[int, int] | None


def plan(width: float, height: float, spec: RenderSpec, vector: bool) -> Plan:
    """Scale from source units (PDF points or image pixels) and the optional center crop of `cover`

    Vector pages scale freely; raster images never upscale and ignore dpi. Every bitmap, including the intermediate
    one of `cover`, is clamped to MAX_IMAGE_SIDE per side.
    """
    cover = spec.fit == Fit.COVER and spec.width is not None and spec.height is not None
    if spec.width and spec.height:
        ratios = (spec.width / width, spec.height / height)
        scale = max(ratios) if cover else min(ratios)
    elif spec.width:
        scale = spec.width / width
    elif spec.height:
        scale = spec.height / height
    else:
        scale = (spec.dpi or DEFAULT_DPI) / _POINTS_PER_INCH if vector else 1.0
    if not vector:
        scale = min(scale, 1.0)
    scale = min(scale, MAX_IMAGE_SIDE / max(width, height))
    if vector:
        # pdfium sizes its bitmap with a plain ceil: a hair less scale keeps exact sizes (800, 4096) exact
        scale *= 1 - _EPSILON
    measure = math.ceil if vector else round
    size = (max(1, measure(width * scale)), max(1, measure(height * scale)))
    if not cover or spec.width is None or spec.height is None:
        return Plan(scale, size, None)
    # largest box of the requested aspect inside the bitmap: the box itself unless no-upscale or the clamp shrank it
    shrink = min(1.0, size[0] / spec.width, size[1] / spec.height)
    crop = (min(size[0], max(1, round(spec.width * shrink))), min(size[1], max(1, round(spec.height * shrink))))
    return Plan(scale, size, crop)


def init_worker() -> None:
    """Pool initializer: pdfium and Pillow loaded before the first task"""
    import pypdfium2  # noqa: F401
    from PIL import Image  # noqa: F401


def render_page(source: str, target: str, spec: RenderSpec) -> Raster:
    """Pool worker: rasterizes one PDF page or image frame and encodes it to `target`"""
    try:
        if Path(source).suffix.lower() == '.pdf':
            image, page_count = _render_pdf(Path(source), spec)
        else:
            image, page_count = _render_image(Path(source), spec)
        _encode(image, Path(target), spec.format)
    except ProblemError:
        raise
    except Exception as e:
        raise ProcessingFailed(f'rendering failed ({type(e).__name__})') from None
    return Raster(width=image.width, height=image.height, page=spec.page, page_count=page_count)


def _render_pdf(source: Path, spec: RenderSpec) -> tuple['Image', int]:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(source)
    except pdfium.PdfiumError:
        raise UnsupportedFormat('PDF could not be opened, it may be corrupted or encrypted') from None
    try:
        count = len(document)
        _check_page(spec.page, count)
        document.init_forms()
        page = document[spec.page - 1]
        width, height = page.get_size()
        layout = plan(width, height, spec, vector=True)
        bitmap = page.render(scale=layout.scale, draw_annots=True, may_draw_forms=True, fill_color=(255, 255, 255, 255))
        image = bitmap.to_pil()
        return _crop(image, layout.crop), count
    finally:
        document.close()


def _render_image(source: Path, spec: RenderSpec) -> tuple['Image', int]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            with Image.open(source) as image:
                count = getattr(image, 'n_frames', 1)
                _check_page(spec.page, count)
                image.seek(spec.page - 1)
                width, height = image.size
                transposed = image.getexif().get(_ORIENTATION_TAG) in _TRANSPOSED
                if transposed:
                    width, height = height, width
                layout = plan(width, height, spec, vector=False)
                if image.format == 'JPEG':
                    # decode at the smallest power of two reduction that still covers the target
                    image.draft('RGB', layout.size[::-1] if transposed else layout.size)
                oriented = ImageOps.exif_transpose(image)
                oriented = oriented.convert('RGBA' if _has_alpha(oriented) else 'RGB')
                if oriented.size != layout.size:
                    oriented = oriented.resize(layout.size, Image.Resampling.LANCZOS)
                return _crop(oriented, layout.crop), count
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ImageTooLarge(f'image exceeds {Image.MAX_IMAGE_PIXELS} pixels') from None
        except (UnidentifiedImageError, OSError, SyntaxError, EOFError):
            raise UnsupportedFormat('image could not be decoded, it may be corrupted or mislabeled') from None


def _check_page(page: int, count: int) -> None:
    if page > count:
        raise PageOutOfRange(f'page {page} is out of range, the document has {count}')


def _has_alpha(image: 'Image') -> bool:
    return image.mode in {'RGBA', 'LA', 'PA', 'RGBa', 'La'} or 'transparency' in image.info


def _crop(image: 'Image', size: tuple[int, int] | None) -> 'Image':
    if size is None:
        return image
    # pdfium rounds its bitmap independently, so never crop past the real edges
    size = (min(size[0], image.width), min(size[1], image.height))
    if size == image.size:
        return image
    left = math.floor((image.width - size[0]) / 2)
    top = math.floor((image.height - size[1]) / 2)
    return image.crop((left, top, left + size[0], top + size[1]))


def _encode(image: 'Image', target: Path, format: ImageFormat) -> None:
    from PIL import Image

    if format == ImageFormat.JPEG and image.mode == 'RGBA':
        flat = Image.new('RGB', image.size, (255, 255, 255))
        flat.paste(image, mask=image.getchannel('A'))
        image = flat
    options = {} if format == ImageFormat.PNG else {'quality': QUALITY}
    image.save(target, format=format.value.upper(), **options)


class RenderService(Service):
    def __init__(self, processes: int, timeout: float = TIMEOUT, max_tasks: int = MAX_TASKS):
        self._pool = ProcessPool('render', processes, timeout=timeout, max_tasks=max_tasks, initializer=init_worker)

    async def warm(self) -> None:
        await self._pool.start()

    async def page(self, source: Path, target: Path, spec: RenderSpec) -> Raster:
        """`source` is a PDF (by its normalized extension) or an image Pillow can decode"""
        with pool_errors():
            return await self._pool.run(render_page, str(source), str(target), spec)

    async def aclose(self) -> None:
        await self._pool.aclose()
