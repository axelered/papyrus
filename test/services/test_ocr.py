import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from server.config import Config
from server.errors import (
    ImageTooLarge,
    OcrFailed,
    OcrNotConfigured,
    OcrRateLimited,
    ProcessingTimeout,
    TooManyPages,
)
from server.io.workspace import Workspace
from server.ocr.base import OcrAdapter, OcrRejected, OcrThrottled, OcrTimeout, OcrTransient, OcrTruncated
from server.services.detect import Document, lookup
from server.services.ocr import ATTEMPTS, MAX_WAIT, OcrService
from server.services.render import Raster, RenderSpec
from test.fixtures.ocr import FakeAdapter

pytestmark = pytest.mark.anyio


class FakeRender:
    """Writes a placeholder JPEG per page; `delay` keeps renders in flight"""

    def __init__(self, pages: int = 1, error: Exception | None = None, delay: float = 0):
        self.pages = pages
        self.error = error
        self.delay = delay
        self.calls: list[int] = []
        self.in_flight = self.peak = 0

    async def page(self, source: Path, target: Path, spec: RenderSpec) -> Raster:
        self.calls.append(spec.page)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
            target.write_bytes(b'jpeg')
            return Raster(width=1, height=1, page=spec.page, page_count=self.pages)
        finally:
            self.in_flight -= 1


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / 'work')


def _service(
    adapter: OcrAdapter,
    render: FakeRender | None = None,
    concurrency: int = 2,
    timeout: float = 5,
    renders: int = 1,
    max_pages: int = 10,
) -> OcrService:
    ocr = {'type': 'qwen', 'model': 'qwen-vl', 'url': 'http://vllm/v1', 'concurrency': concurrency}
    config = Config.model_validate({'ocr': ocr | {'max_pages': max_pages}}).ocr
    fake = cast(Any, render or FakeRender())
    return OcrService(
        config, fake, cast(Any, None), cast(Any, None), renders=renders, adapter=adapter, timeout=timeout, backoff=0
    )


def _pdf(tmp_path: Path) -> Document:
    return Document(tmp_path / 'scan.pdf', lookup('pdf'))


async def _read(service: OcrService, workspace: Workspace, tmp_path: Path) -> str:
    target = workspace.new_file('scan.md')
    await service.read(_pdf(tmp_path), workspace, target)
    return target.read_text(encoding='utf-8')


async def _settle(done: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not done():
            await asyncio.sleep(0.005)


def _images(workspace: Workspace) -> list[Path]:
    return list(workspace.path.rglob('*.jpg'))


async def test_reads_every_page_in_order(workspace: Workspace, tmp_path: Path):
    service = _service(FakeAdapter(), FakeRender(pages=3))

    content = await _read(service, workspace, tmp_path)

    assert content == '\n\n'.join(f'<!-- Page number: {n} -->\n\nread page-{n}.jpg' for n in (1, 2, 3))
    assert _images(workspace) == []


async def test_reports_the_page_count(workspace: Workspace, tmp_path: Path):
    service = _service(FakeAdapter(), FakeRender(pages=2))

    result = await service.read(_pdf(tmp_path), workspace, workspace.new_file('scan.md'))

    assert (result.page_count, result.width, result.title) == (2, None, None)


async def test_too_many_pages_never_reach_the_backend(workspace: Workspace, tmp_path: Path):
    adapter = FakeAdapter()

    with pytest.raises(TooManyPages, match='3 pages'):
        await _read(_service(adapter, FakeRender(pages=3), max_pages=2), workspace, tmp_path)

    assert adapter.calls == 0
    assert _images(workspace) == []


@pytest.mark.parametrize(
    ('answer', 'page'),
    [
        ('<!-- Page number: 9 -->\ntext\n<!-- Slide number: 2 -->', 'text'),
        ('  \n# Title\r\ntext\n ', '# Title\ntext'),
        ('<!-- a real comment -->\ntext', '<!-- a real comment -->\ntext'),
    ],
)
async def test_normalizes_the_answer(answer: str, page: str, workspace: Workspace, tmp_path: Path):
    service = _service(FakeAdapter(answer=lambda _: answer))

    assert await _read(service, workspace, tmp_path) == f'<!-- Page number: 1 -->\n\n{page}'


@pytest.mark.parametrize('error', [OcrTransient('down'), OcrTimeout('slow'), OcrThrottled('busy', 0.01)])
async def test_retries_and_renders_once(
    error: Exception, workspace: Workspace, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    adapter = FakeAdapter(error, error)
    render = FakeRender()

    caplog.set_level(logging.WARNING, logger='papyrus.ocr')
    assert await _read(_service(adapter, render), workspace, tmp_path) == '<!-- Page number: 1 -->\n\nread page-1.jpg'

    assert (adapter.calls, render.calls) == (ATTEMPTS, [1])
    assert 'page 1 attempt 1' in caplog.text


async def test_later_page_retries_without_rendering_again(workspace: Workspace, tmp_path: Path):
    failed: list[str] = []

    def answer(image: Path) -> str:
        if image.name == 'page-2.jpg' and not failed:
            failed.append(image.name)
            raise OcrTransient('down')
        return image.name

    render = FakeRender(pages=2)

    await _read(_service(FakeAdapter(answer=answer), render, concurrency=1), workspace, tmp_path)

    assert (failed, render.calls) == (['page-2.jpg'], [1, 2])


@pytest.mark.parametrize(
    ('error', 'calls', 'problem'),
    [
        (OcrTransient('down'), ATTEMPTS, OcrFailed),
        (OcrTimeout('slow'), ATTEMPTS, ProcessingTimeout),
        (OcrThrottled('busy', 0), ATTEMPTS, OcrRateLimited),
        (OcrThrottled('busy', MAX_WAIT + 1), 1, OcrRateLimited),
        (OcrRejected('refused'), 1, OcrFailed),
        (OcrTruncated('cut'), 1, OcrFailed),
    ],
)
async def test_maps_final_errors_to_problems(
    error: Exception, calls: int, problem: type[Exception], workspace: Workspace, tmp_path: Path
):
    adapter = FakeAdapter(*[error] * ATTEMPTS)

    with pytest.raises(problem, match=f'page 1: {error}'):
        await _read(_service(adapter), workspace, tmp_path)

    assert adapter.calls == calls
    assert _images(workspace) == []


async def test_slow_call_times_out(workspace: Workspace, tmp_path: Path):
    adapter = FakeAdapter(gate=asyncio.Event())

    with pytest.raises(ProcessingTimeout):
        await _read(_service(adapter, timeout=0.01), workspace, tmp_path)

    assert (adapter.calls, adapter.in_flight) == (ATTEMPTS, 0)


async def test_render_error_is_not_retried(workspace: Workspace, tmp_path: Path):
    adapter = FakeAdapter()
    render = FakeRender(pages=2, error=ImageTooLarge('huge'))

    with pytest.raises(ImageTooLarge):
        await _read(_service(adapter, render), workspace, tmp_path)

    assert (adapter.calls, render.calls) == (0, [1])


async def test_slots_bound_calls_and_renders_waiting(workspace: Workspace, tmp_path: Path):
    gate = asyncio.Event()
    adapter = FakeAdapter(gate=gate)
    render = FakeRender(pages=5)
    task = asyncio.create_task(_read(_service(adapter, render, concurrency=2), workspace, tmp_path))

    await _settle(lambda: adapter.in_flight == 2)
    assert len(render.calls) == 2
    gate.set()
    await task

    assert (adapter.peak, len(render.calls)) == (2, 5)


@pytest.mark.parametrize(('renders', 'peak'), [(1, 1), (3, 3)])
async def test_renders_are_capped_below_the_slots(renders: int, peak: int, workspace: Workspace, tmp_path: Path):
    render = FakeRender(pages=4, delay=0.02)

    await _read(_service(FakeAdapter(), render, concurrency=4, renders=renders), workspace, tmp_path)

    assert render.peak == peak


async def test_slot_is_free_while_waiting_for_a_retry(workspace: Workspace, tmp_path: Path):
    service = _service(FakeAdapter(OcrThrottled('busy', 0.3)), concurrency=1)

    waiting = asyncio.create_task(_read(service, workspace, tmp_path))
    await asyncio.sleep(0.05)
    async with asyncio.timeout(0.2):
        await _read(service, Workspace(tmp_path / 'other'), tmp_path)

    await waiting


async def test_cancelled_read_frees_its_slot(workspace: Workspace, tmp_path: Path):
    adapter = FakeAdapter(gate=asyncio.Event())
    service = _service(adapter, concurrency=1)
    task = asyncio.create_task(_read(service, workspace, tmp_path))
    await _settle(lambda: adapter.in_flight == 1)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    adapter.gate = None

    assert 'read page-1.jpg' in await _read(service, Workspace(tmp_path / 'other'), tmp_path)


async def test_accepts_formats_with_pages():
    service = _service(FakeAdapter())

    assert [service.accepts(lookup(label)) for label in ('pdf', 'png', 'docx', 'xlsx', 'csv')] == [
        True,
        True,
        True,
        False,
        False,
    ]


async def test_unconfigured_service_warms_and_rejects():
    service = OcrService(None, cast(Any, None), cast(Any, None), cast(Any, None), renders=1)
    await service.warm()

    assert not service.configured
    with pytest.raises(OcrNotConfigured):
        _ = service.config
    await service.aclose()


@pytest.mark.parametrize(
    ('ocr', 'adapter'),
    [
        ({'type': 'qwen', 'model': 'qwen-vl', 'url': 'http://unreachable.invalid/v1'}, 'QwenAdapter'),
        ({'type': 'mistral', 'api_key': 'secret'}, 'MistralAdapter'),
    ],
)
async def test_warm_builds_the_configured_adapter_offline(ocr: dict[str, str], adapter: str):
    config = Config.model_validate({'ocr': ocr}).ocr
    async with OcrService(config, cast(Any, None), cast(Any, None), cast(Any, None), renders=1) as service:
        await service.warm()

        assert service.configured
        assert type(service.adapter).__name__ == adapter


async def test_close_closes_the_adapter():
    adapter = FakeAdapter()

    await _service(adapter).aclose()

    assert adapter.closed
