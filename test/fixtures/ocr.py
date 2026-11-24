import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest
from PIL import Image

from server.ocr.base import OcrAdapter
from test.fixtures.samples import image

Handler = Callable[[httpx2.Request], httpx2.Response]


@pytest.fixture
def page_image(tmp_path: Path) -> Path:
    path = tmp_path / 'page.jpg'
    path.write_bytes(image('JPEG'))
    return path


def mock_client(handler: Handler) -> httpx2.AsyncClient:
    """HTTP client for the real OCR SDKs, which use httpx2"""
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


class FakeAdapter(OcrAdapter):
    """Raises the scripted errors first, then answers; `gate` holds calls in flight"""

    def __init__(
        self,
        *errors: Exception,
        gate: asyncio.Event | None = None,
        answer: Callable[[Path], str] = lambda image: f'read {image.name}',
    ):
        self.answer = answer
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
            return self.answer(image)
        finally:
            self.in_flight -= 1

    async def aclose(self) -> None:
        self.closed = True


def image_size(image: Path) -> str:
    """Answer that identifies the page it was rendered from"""
    with Image.open(image) as page:
        return f'{page.width}x{page.height}'
