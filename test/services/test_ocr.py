import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from server.config import Config
from server.errors import ImageTooLarge, OcrFailed, OcrNotConfigured, OcrRateLimited, ProcessingTimeout
from server.ocr.base import OcrAdapter, OcrRejected, OcrTimeout, OcrTransient, OcrTruncated
from server.ocr.base import OcrRateLimited as BackendRateLimited
from server.services.ocr import ATTEMPTS, MAX_WAIT, OcrService

pytestmark = pytest.mark.anyio

IMAGE = Path('page.jpg')


class FakeAdapter(OcrAdapter):
    """Raises the scripted errors first, then answers; `gate` holds calls in flight"""

    def __init__(self, *errors: Exception, gate: asyncio.Event | None = None):
        self.errors = list(errors)
        self.gate = gate
        self.calls = self.in_flight = self.peak = 0
        self.closed = False

    async def read(self, image: Path) -> str:
        self.calls += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.errors:
                raise self.errors.pop(0)
            return f'read {image.name}'
        finally:
            self.in_flight -= 1

    async def aclose(self) -> None:
        self.closed = True


def _service(adapter: OcrAdapter, concurrency: int = 2, timeout: float = 5) -> OcrService:
    ocr = {'type': 'qwen', 'model': 'qwen-vl', 'url': 'http://vllm/v1', 'concurrency': concurrency}
    config = Config.model_validate({'ocr': ocr}).ocr
    return OcrService(config, renders=1, adapter=adapter, timeout=timeout, backoff=0)


async def _page() -> Path:
    return IMAGE


async def _settle(done: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not done():
            await asyncio.sleep(0.005)


async def test_reads_a_page():
    assert await _service(FakeAdapter()).read(1, _page) == 'read page.jpg'


@pytest.mark.parametrize('error', [OcrTransient('down'), OcrTimeout('slow'), BackendRateLimited('busy', 0.01)])
async def test_retries_and_renders_once(error: Exception, caplog: pytest.LogCaptureFixture):
    adapter = FakeAdapter(error, error)
    renders = 0

    async def page() -> Path:
        nonlocal renders
        renders += 1
        return IMAGE

    caplog.set_level(logging.WARNING, logger='papyrus.ocr')
    assert await _service(adapter).read(3, page) == 'read page.jpg'

    assert (adapter.calls, renders) == (ATTEMPTS, 1)
    assert 'page 3 attempt 1' in caplog.text


@pytest.mark.parametrize(
    ('error', 'calls', 'problem'),
    [
        (OcrTransient('down'), ATTEMPTS, OcrFailed),
        (OcrTimeout('slow'), ATTEMPTS, ProcessingTimeout),
        (BackendRateLimited('busy', 0), ATTEMPTS, OcrRateLimited),
        (BackendRateLimited('busy', MAX_WAIT + 1), 1, OcrRateLimited),
        (OcrRejected('refused'), 1, OcrFailed),
        (OcrTruncated('cut'), 1, OcrFailed),
    ],
)
async def test_maps_final_errors_to_problems(error: Exception, calls: int, problem: type[Exception]):
    adapter = FakeAdapter(*[error] * ATTEMPTS)

    with pytest.raises(problem, match=f'page 7: {error}'):
        await _service(adapter).read(7, _page)

    assert adapter.calls == calls


async def test_slow_call_times_out():
    adapter = FakeAdapter(gate=asyncio.Event())

    with pytest.raises(ProcessingTimeout):
        await _service(adapter, timeout=0.01).read(1, _page)

    assert (adapter.calls, adapter.in_flight) == (ATTEMPTS, 0)


async def test_render_error_is_not_retried():
    adapter = FakeAdapter()
    renders = 0

    async def page() -> Path:
        nonlocal renders
        renders += 1
        raise ImageTooLarge('huge')

    with pytest.raises(ImageTooLarge):
        await _service(adapter).read(1, page)

    assert (adapter.calls, renders) == (0, 1)


async def test_slots_bound_calls_and_renders_waiting():
    gate = asyncio.Event()
    adapter = FakeAdapter(gate=gate)
    service = _service(adapter, concurrency=2)
    rendered = 0

    async def page() -> Path:
        nonlocal rendered
        rendered += 1
        return IMAGE

    tasks = [asyncio.create_task(service.read(number, page)) for number in range(1, 6)]
    await _settle(lambda: adapter.in_flight == 2)
    assert rendered == 2
    gate.set()
    await asyncio.gather(*tasks)

    assert (adapter.peak, rendered) == (2, 5)


async def test_slot_is_free_while_waiting_for_a_retry():
    service = _service(FakeAdapter(BackendRateLimited('busy', 0.3)), concurrency=1)

    waiting = asyncio.create_task(service.read(1, _page))
    await asyncio.sleep(0.05)
    async with asyncio.timeout(0.2):
        assert await service.read(2, _page) == 'read page.jpg'

    assert await waiting == 'read page.jpg'


async def test_cancelled_read_frees_its_slot():
    adapter = FakeAdapter(gate=asyncio.Event())
    service = _service(adapter, concurrency=1)
    task = asyncio.create_task(service.read(1, _page))
    await _settle(lambda: adapter.in_flight == 1)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    adapter.gate = None

    assert await service.read(2, _page) == 'read page.jpg'


async def test_rendering_is_capped():
    service = _service(FakeAdapter())

    async with service.rendering():
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.02), service.rendering():
                pytest.fail('the render slot must be taken')


async def test_unconfigured_service_warms_and_rejects():
    service = OcrService(None, renders=1)
    await service.warm()

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
    async with OcrService(Config.model_validate({'ocr': ocr}).ocr, renders=1) as service:
        await service.warm()

        assert type(service.adapter).__name__ == adapter


async def test_close_closes_the_adapter():
    adapter = FakeAdapter()

    await _service(adapter).aclose()

    assert adapter.closed
