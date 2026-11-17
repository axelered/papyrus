import base64
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fsspec import AbstractFileSystem
from PIL import Image

from server.services.browser import Capture
from server.types.screenshot import ScreenshotOptions
from test.api.test_render_convert import FakePdfConvert
from test.fixtures.samples import image

HTML = b'<html><head><title>Page</title></head><body><h1>Hello</h1></body></html>'


class FakeBrowser:
    """Writes a viewport sized PNG like the real screenshot would"""

    def __init__(self):
        self.calls: list[tuple[str, ScreenshotOptions, bytes | None]] = []

    async def screenshot(
        self, url: str, target: Path, options: ScreenshotOptions, document: Path | None = None
    ) -> Capture:
        self.calls.append((url, options, document.read_bytes() if document else None))
        target.write_bytes(image('PNG', size=(options.width, options.height)))
        return Capture(width=options.width, height=options.height, url=url, status=200)

    async def aclose(self) -> None:
        pass


def _install(client: TestClient) -> tuple[FakeBrowser, FakePdfConvert]:
    browser, convert = FakeBrowser(), FakePdfConvert()
    client.app.state.services.browser = browser
    client.app.state.services.convert = convert
    return browser, convert


def _upload(client: TestClient, route: str, headers: dict[str, str], metadata: str = '{}'):
    return client.post(
        f'/v1/{route}', files={'file': ('page.html', HTML)}, data={'metadata': metadata}, headers=headers
    )


def test_uploaded_html_thumbnail_is_a_screenshot(client: TestClient, auth_headers: dict[str, str]):
    browser, convert = _install(client)

    response = _upload(client, 'thumbnail', auth_headers, '{"output": "json"}')

    assert response.status_code == 200, response.text
    body = response.json()
    assert browser.calls == [('http://document.invalid/page.html', ScreenshotOptions(width=1280, height=1280), HTML)]
    assert convert.calls == []
    assert (body['width'], body['height'], body['page'], body['page_count']) == (256, 256, 1, 1)
    assert body['source_type']['label'] == 'html'


@pytest.mark.parametrize(
    ('box', 'viewport', 'size'),
    [
        ((640, 400), (1280, 800), (640, 400)),
        ((300, 200), (1280, 853), (300, 200)),
        ((200, 300), (1280, 1920), (200, 300)),
        ((100, 1000), (1280, 2160), (100, 169)),
        ((1000, 100), (1280, 400), (320, 100)),
    ],
)
def test_viewport_follows_the_box_shape(
    client: TestClient, auth_headers: dict[str, str], box: tuple, viewport: tuple, size: tuple
):
    browser, _ = _install(client)
    metadata = f'{{"options": {{"width": {box[0]}, "height": {box[1]}, "format": "png"}}}}'

    response = _upload(client, 'thumbnail', auth_headers, metadata)

    options = browser.calls[0][1]
    assert (options.width, options.height) == viewport
    assert Image.open(io.BytesIO(response.content)).size == size


def test_cover_thumbnail_crops_the_screenshot(client: TestClient, auth_headers: dict[str, str]):
    _install(client)

    response = _upload(client, 'thumbnail', auth_headers, '{"options": {"fit": "cover", "format": "png"}}')

    assert Image.open(io.BytesIO(response.content)).size == (256, 256)


def test_connector_html_gets_the_synthetic_origin(
    client: TestClient, auth_headers: dict[str, str], memory_fs: AbstractFileSystem
):
    browser, _ = _install(client)
    memory_fs.pipe('site/My Page.html', HTML)

    client.post('/v1/thumbnail', json={'source': 'memory://mem/site/My Page.html'}, headers=auth_headers)

    assert browser.calls[0][0] == 'http://document.invalid/My%20Page.html'


def test_fetched_html_keeps_its_url_as_origin(client: TestClient, auth_headers: dict[str, str]):
    browser, _ = _install(client)
    transfer = client.app.state.transfer

    async def materialize(uri: str, workspace, parts) -> Path:
        path = workspace.new_file('index.html')
        path.write_bytes(HTML)
        return path

    transfer.materialize = materialize

    response = client.post('/v1/thumbnail', json={'source': 'https://axelered.com/'}, headers=auth_headers)

    assert response.status_code == 200, response.text
    assert browser.calls[0][0] == 'https://axelered.com/'


def test_html_has_one_page(client: TestClient, auth_headers: dict[str, str]):
    browser, _ = _install(client)

    response = _upload(client, 'thumbnail', auth_headers, '{"options": {"page": 2}}')

    assert (response.status_code, response.json()['code']) == (422, 'page_out_of_range')
    assert 'has 1' in response.json()['detail']
    assert browser.calls == []


def test_render_route_keeps_libreoffice_for_html(client: TestClient, auth_headers: dict[str, str]):
    browser, convert = _install(client)

    response = _upload(client, 'render', auth_headers)

    assert response.status_code == 200, response.text
    assert browser.calls == []
    assert len(convert.calls) == 1


# - - - End to end with Chromium


@pytest.mark.full
def test_real_html_thumbnail(client: TestClient, auth_headers: dict[str, str]):
    response = _upload(client, 'thumbnail', auth_headers, '{"output": "json"}')

    assert response.status_code == 200, response.text
    body = response.json()
    thumbnail = Image.open(io.BytesIO(base64.b64decode(body['data'])))
    assert (thumbnail.format, thumbnail.size) == ('WEBP', (256, 256))
