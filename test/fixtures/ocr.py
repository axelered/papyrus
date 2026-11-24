from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

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
