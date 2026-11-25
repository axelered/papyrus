import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt, wait_random_exponential

from server.config import MistralConfig, OcrConfig
from server.errors import (
    OcrFailed,
    OcrNotConfigured,
    OcrRateLimited,
    PageOutOfRange,
    ProcessingTimeout,
    TooManyPages,
    UnsupportedFormat,
)
from server.io.workspace import Workspace
from server.ocr.base import OcrAdapter, OcrError, OcrTimeout, OcrTransient
from server.ocr.base import OcrRateLimited as BackendRateLimited
from server.services.base import Service
from server.services.convert import ConvertService
from server.services.detect import Document, Format
from server.services.markdown import MarkdownService
from server.services.render import Raster, RenderService, RenderSpec
from server.types.common import FormatGroup, FormatLabel, ImageFormat
from server.types.convert import ConvertFormat
from server.types.markdown import ChunkingOptions, Extracted
from server.types.render import Fit
from server.utils.problem import ProblemError
from server.utils.timing import stage

TIMEOUT = 120
ATTEMPTS = 3
# longest wait between attempts; a backend asking for more fails at once
MAX_WAIT = 30

# formats with pages rendered through LibreOffice; spreadsheets are left out, pagination cuts them
CONVERSIONS = frozenset(
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
IMAGES = frozenset(
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
FORMATS = CONVERSIONS | IMAGES | {FormatLabel.PDF}
# the frames of other images are an animation, not pages
SINGLE_PAGE = IMAGES - {FormatLabel.TIFF}

logger = logging.getLogger('papyrus.ocr')


class OcrService(Service):
    """Reads documents from their page images with the configured backend (adr/ocr.md)"""

    def __init__(
        self,
        config: OcrConfig | None,
        render: RenderService,
        convert: ConvertService,
        markdown: MarkdownService,
        renders: int,
        adapter: OcrAdapter | None = None,
        timeout: float = TIMEOUT,
        backoff: float = 1,
    ):
        self._config = config
        self._render = render
        self._convert = convert
        self._markdown = markdown
        self._adapter = adapter
        self._timeout = timeout
        self._backoff = wait_random_exponential(multiplier=backoff, max=MAX_WAIT)
        self._slots = asyncio.Semaphore(config.concurrency if config else 1)
        # held around every OCR render, so slow documents cannot take the whole shared render pool
        self._renders = asyncio.Semaphore(renders)

    @property
    def configured(self) -> bool:
        return self._config is not None

    @property
    def config(self) -> OcrConfig:
        if self._config is None:
            raise OcrNotConfigured('readMode ocr needs an OCR backend, this server has none')
        return self._config

    @property
    def adapter(self) -> OcrAdapter:
        if self._adapter is None:
            self._adapter = _create(self.config)
        return self._adapter

    def accepts(self, value: Format) -> bool:
        """Formats with pages; the others have no page image to read"""
        return value.label in FORMATS

    async def warm(self) -> None:
        """Builds the client and never calls the backend: an OCR outage must not stop startup"""
        if self._config is not None:
            _ = self.adapter

    async def read(
        self,
        document: Document,
        workspace: Workspace,
        target: Path,
        chunking: ChunkingOptions | None = None,
        chunks_target: Path | None = None,
    ) -> Extracted:
        """Writes the pages read by the backend to `target` and chunks them (adr/ocr.md, Scheduling)"""
        source = await self._pdf(document, workspace)
        with stage('render'):
            try:
                first, raster = await self._render_page(source, workspace, 1)
            except PageOutOfRange:
                raise UnsupportedFormat('the document has no pages') from None
        count = 1 if document.format.label in SINGLE_PAGE else raster.page_count or 1
        if count > self.config.max_pages:
            first.unlink(missing_ok=True)
            raise TooManyPages(f'the document has {count} pages, at most {self.config.max_pages} can be read')

        async def read(number: int) -> str:
            return await self._read_page(source, workspace, number, first if number == 1 else None)

        with stage('ocr'):
            pages = await _in_order(read, count, self.config.concurrency)
        unit = 'Slide' if document.format.group == FormatGroup.PRESENTATION else 'Page'
        markdown = '\n\n'.join(
            f'<!-- {unit} number: {number} -->\n\n{text}'.rstrip() for number, text in enumerate(pages, start=1)
        )
        target.write_text(markdown, encoding='utf-8', newline='')
        if chunking is not None and chunks_target is not None:
            with stage('chunk'):
                await self._markdown.chunk(target, chunks_target, chunking)
        width, height = raster.source_size or (None, None)
        return Extracted(page_count=raster.page_count, width=width, height=height)

    async def aclose(self) -> None:
        adapter, self._adapter = self._adapter, None
        if adapter is not None:
            await adapter.aclose()

    async def _pdf(self, document: Document, workspace: Workspace) -> Path:
        """Office documents as PDF, PDF and images as they are"""
        if document.format.label not in CONVERSIONS:
            return document.path
        pdf = workspace.new_file(f'{document.path.stem}.pdf')
        with stage('convert'):
            await self._convert.convert(document.path, pdf, ConvertFormat.PDF)
        return pdf

    async def _render_page(self, source: Path, workspace: Workspace, number: int) -> tuple[Path, Raster]:
        spec = RenderSpec(
            page=number,
            width=None,
            height=None,
            dpi=self.adapter.dpi,
            fit=Fit.CONTAIN,
            format=ImageFormat.JPEG,
            max_pixels=self.adapter.max_pixels,
        )
        image = workspace.new_file(f'page-{number}.jpg')
        async with self._renders:
            return image, await self._render.page(source, image, spec)

    async def _read_page(self, source: Path, workspace: Workspace, number: int, image: Path | None) -> str:
        """Markdown of page `number`, rendered inside the slot so the slots also bound the renders waiting

        The image is kept across attempts: a retry does not render again, and the slot is free while waiting.
        """
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(ATTEMPTS),
                wait=self._wait,
                retry=retry_if_exception(_retryable),
                before_sleep=_log_retry(number),
                reraise=True,
            ):
                with attempt:
                    async with self._slots:
                        if image is None:
                            image, _ = await self._render_page(source, workspace, number)
                        return await self._call(image)
        except OcrError as e:
            raise _problem(number, e) from e
        finally:
            if image is not None:
                image.unlink(missing_ok=True)
        raise AssertionError('unreachable: the last attempt returns or raises')

    async def _call(self, image: Path) -> str:
        try:
            async with asyncio.timeout(self._timeout):
                return await self.adapter.read(image)
        except TimeoutError:
            raise OcrTimeout(f'the OCR backend exceeded {self._timeout:g}s') from None

    def _wait(self, state: RetryCallState) -> float:
        error = _error(state)
        if isinstance(error, BackendRateLimited) and error.retry_after is not None:
            return error.retry_after
        return self._backoff(state)


async def _in_order(read: Callable[[int], Awaitable[str]], count: int, concurrency: int) -> list[str]:
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


def _create(config: OcrConfig) -> OcrAdapter:
    # imported here: only the SDK of the configured backend is loaded
    if isinstance(config, MistralConfig):
        from server.ocr.mistral import MistralAdapter

        return MistralAdapter(config.model, config.api_key.get_secret_value(), config.url)
    from server.ocr.qwen import QwenAdapter

    api_key = config.api_key.get_secret_value() if config.api_key else None
    return QwenAdapter(config.model, config.url, api_key, config.extra_body)


def _retryable(error: BaseException) -> bool:
    if isinstance(error, BackendRateLimited):
        return error.retry_after is None or error.retry_after <= MAX_WAIT
    return isinstance(error, (OcrTransient, OcrTimeout))


def _error(state: RetryCallState) -> BaseException | None:
    return state.outcome.exception() if state.outcome else None


def _log_retry(number: int) -> Callable[[RetryCallState], None]:
    def log(state: RetryCallState) -> None:
        delay = state.next_action.sleep if state.next_action else 0
        logger.warning('page %s attempt %s: %s, retry in %.1fs', number, state.attempt_number, _error(state), delay)

    return log


def _problem(number: int, error: OcrError) -> ProblemError:
    detail = f'page {number}: {error}'
    if isinstance(error, BackendRateLimited):
        return OcrRateLimited(detail)
    if isinstance(error, OcrTimeout):
        return ProcessingTimeout(detail)
    return OcrFailed(detail)
