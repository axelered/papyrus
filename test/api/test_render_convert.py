import base64
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.errors import PageOutOfRange
from server.types.convert import ConvertFormat
from test.fixtures import samples

A4 = (595, 842)


class FakePdfConvert:
    """Stands in for LibreOffice on the host: writes a one-page A4 PDF"""

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[str, ConvertFormat, int | None]] = []
        self.error = error

    async def convert(self, source: Path, target: Path, format: ConvertFormat, page: int | None = None) -> None:
        self.calls.append((source.suffix, format, page))
        if self.error is not None:
            raise self.error
        target.write_bytes(samples.pdf_pages(A4))

    def close(self) -> None:
        pass


def _install(client: TestClient, fake: FakePdfConvert) -> FakePdfConvert:
    client.app.state.services.convert = fake
    return fake


def _upload(client: TestClient, route: str, headers: dict[str, str], name: str, content: bytes, metadata: str = '{}'):
    return client.post(f'/v1/{route}', files={'file': (name, content)}, data={'metadata': metadata}, headers=headers)


@pytest.mark.parametrize(
    ('name', 'content', 'suffix'),
    [
        ('report.docx', samples.docx(), '.docx'),
        ('page.html', samples.LONG_HTML, '.html'),
        ('deck.pptx', samples.pptx(), '.pptx'),
    ],
)
def test_office_and_html_render_through_a_single_page_pdf(
    client: TestClient, auth_headers: dict[str, str], name: str, content: bytes, suffix: str
):
    fake = _install(client, FakePdfConvert())

    response = _upload(client, 'render', auth_headers, name, content, '{"output": "json", "options": {"page": 3}}')

    assert response.status_code == 200, response.text
    body = response.json()
    assert fake.calls == [(suffix, ConvertFormat.PDF, 3)]
    assert body['page'] == 3
    assert 'page_count' not in body
    assert (body['width'], body['height']) == (1240, 1755)


def test_office_thumbnail_file(client: TestClient, auth_headers: dict[str, str]):
    _install(client, FakePdfConvert())

    response = _upload(client, 'thumbnail', auth_headers, 'report.docx', samples.docx())

    assert 'filename="report.webp"' in response.headers['content-disposition']
    assert Image.open(io.BytesIO(response.content)).size == (181, 256)


def test_out_of_range_from_conversion(client: TestClient, auth_headers: dict[str, str]):
    _install(client, FakePdfConvert(PageOutOfRange('page 9 is out of range')))

    response = _upload(client, 'render', auth_headers, 'report.docx', samples.docx(), '{"options": {"page": 9}}')

    assert (response.status_code, response.json()['code']) == (422, 'page_out_of_range')


# - - - End to end with LibreOffice


@pytest.mark.full
@pytest.mark.parametrize(
    ('name', 'content'),
    [
        ('report.docx', samples.docx_pages(3)),
        ('deck.pptx', samples.pptx_slides(3)),
        ('sheet.xlsx', samples.xlsx()),
        ('page.html', samples.LONG_HTML),
    ],
)
def test_renders_real_office_pages(client: TestClient, auth_headers: dict[str, str], name: str, content: bytes):
    response = _upload(client, 'thumbnail', auth_headers, name, content, '{"output": "json"}')

    assert response.status_code == 200, response.text
    body = response.json()
    assert max(body['width'], body['height']) == 256
    assert Image.open(io.BytesIO(base64.b64decode(body['data']))).format == 'WEBP'


@pytest.mark.full
def test_real_page_past_the_end(client: TestClient, auth_headers: dict[str, str]):
    response = _upload(client, 'render', auth_headers, 'report.docx', samples.docx_pages(2), '{"options": {"page": 5}}')

    assert (response.status_code, response.json()['code']) == (422, 'page_out_of_range')
