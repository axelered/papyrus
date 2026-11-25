import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fsspec import AbstractFileSystem

from server.config import Config
from server.ocr.base import OcrAdapter, OcrRateLimited, OcrRejected
from server.services.convert import ConvertService
from server.services.ocr import OcrService
from server.types.convert import ConvertFormat
from test.fixtures import samples
from test.fixtures.documents import BINARY, CSV
from test.fixtures.ocr import FakeAdapter, image_size

MAX_PAGES = 4
# page sizes in points that render to distinct pixels at 200 dpi
SIZES = [(72, 72), (72, 144), (72, 216)]
PIXELS = ['200x200', '200x400', '200x600']

Install = Callable[[OcrAdapter], OcrService]


class FakePdfConvert:
    """Stands in for LibreOffice on the host: PDF exports have the pages of SIZES, OOXML targets are samples"""

    # the real method, over the fake convert
    modernize = ConvertService.modernize

    def __init__(self):
        self.calls: list[tuple[str, ConvertFormat]] = []

    async def convert(self, source: Path, target: Path, format: ConvertFormat) -> None:
        self.calls.append((source.suffix, format))
        ooxml = {ConvertFormat.DOCX: samples.docx, ConvertFormat.PPTX: samples.pptx, ConvertFormat.XLSX: samples.xlsx}
        target.write_bytes(ooxml[format]() if format in ooxml else samples.pdf_pages(*SIZES))


@pytest.fixture
def install(client: TestClient, request: pytest.FixtureRequest) -> Install:
    def install(adapter: OcrAdapter, concurrency: int = 2) -> OcrService:
        ocr = {'type': 'qwen', 'model': 'm', 'url': 'http://vllm/v1', 'max_pages': MAX_PAGES}
        config = Config.model_validate({'ocr': ocr | {'concurrency': concurrency}}).ocr
        services = client.app.state.services
        # the fake LibreOffice of tests that ask for it, whatever the fixture order
        convert = request.getfixturevalue('convert') if 'convert' in request.fixturenames else services.convert
        service = OcrService(config, services.render, convert, services.markdown, renders=1, adapter=adapter, backoff=0)
        client.app.state.services.ocr = service
        return service

    return install


@pytest.fixture
def adapter(install: Install) -> FakeAdapter:
    adapter = FakeAdapter(answer=image_size)
    install(adapter)
    return adapter


@pytest.fixture
def convert(client: TestClient) -> FakePdfConvert:
    fake = FakePdfConvert()
    client.app.state.services.convert = fake
    return fake


def _post(client: TestClient, headers: dict[str, str], name: str, content: bytes, options: Any = None, **fields: Any):
    metadata = {'options': {'readMode': 'ocr', **(options or {})}, **fields}
    return client.post(
        '/v1/markdown', headers=headers, data={'metadata': json.dumps(metadata)}, files={'file': (name, content)}
    )


def _leftovers(work_dir: Path) -> list[Path]:
    return list(work_dir.iterdir()) if work_dir.exists() else []


def _pages(*texts: str, unit: str = 'Page') -> str:
    return '\n\n'.join(f'<!-- {unit} number: {n} -->\n\n{text}'.rstrip() for n, text in enumerate(texts, start=1))


# - - - Pages


def test_reads_every_pdf_page_in_order(
    client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter, work_dir: Path
):
    response = _post(client, auth_headers, 'scan.pdf', samples.pdf_pages(*SIZES))

    body = response.json()
    assert response.status_code == 200, response.text
    assert body['result']['content'] == _pages(*PIXELS)
    assert body['source']['pageCount'] == 3
    assert 'title' not in body['source'] and 'width' not in body['source']
    assert body['options'] == {'readMode': 'ocr'}
    assert adapter.calls == 3
    assert _leftovers(work_dir) == []


def test_pages_stay_in_order_when_answers_do_not(client: TestClient, auth_headers: dict[str, str], install: Install):
    class SlowFirstPage(FakeAdapter):
        async def read(self, image: Path) -> str:
            if image_size(image) == PIXELS[0]:
                await asyncio.sleep(0.3)
            return await super().read(image)

    install(SlowFirstPage(answer=image_size))

    body = _post(client, auth_headers, 'scan.pdf', samples.pdf_pages(*SIZES)).json()

    assert body['result']['content'] == _pages(*PIXELS)


def test_blank_page_keeps_its_marker(client: TestClient, auth_headers: dict[str, str], install: Install):
    install(FakeAdapter(answer=lambda _: ''))

    content = _post(client, auth_headers, 'scan.pdf', samples.pdf_pages(*SIZES[:2])).json()['result']['content']

    assert content == '<!-- Page number: 1 -->\n\n<!-- Page number: 2 -->'


def test_reads_images(client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter):
    body = _post(client, auth_headers, 'photo.png', samples.image('PNG', (64, 48))).json()

    assert body['result']['content'] == _pages('64x48')
    assert (body['source']['width'], body['source']['height'], body['source']['pageCount']) == (64, 48, 1)


def test_reads_every_tiff_frame(client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter):
    body = _post(client, auth_headers, 'fax.tiff', samples.image('TIFF', frames=3)).json()

    assert adapter.calls == body['source']['pageCount'] == 3


@pytest.mark.parametrize('format', ['GIF', 'WEBP'])
def test_reads_only_the_first_frame_of_animations(
    client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter, format: str
):
    # more frames than MAX_PAGES would allow pages
    content = samples.image(format, frames=MAX_PAGES + 1)

    response = _post(client, auth_headers, f'loop.{format.lower()}', content)

    assert response.status_code == 200, response.text
    assert adapter.calls == 1


@pytest.mark.parametrize(
    ('name', 'content', 'unit'),
    [('report.docx', samples.docx(), 'Page'), ('deck.pptx', samples.pptx(), 'Slide'), ('a.odt', samples.odt(), 'Page')],
)
def test_office_goes_through_pdf(
    client: TestClient,
    auth_headers: dict[str, str],
    adapter: FakeAdapter,
    convert: FakePdfConvert,
    name: str,
    content: bytes,
    unit: str,
):
    body = _post(client, auth_headers, name, content).json()

    # legacy formats are modernized first
    modernized = [('.odt', ConvertFormat.DOCX), ('.docx', ConvertFormat.PDF)]
    assert convert.calls == (modernized if name.endswith('.odt') else [(Path(name).suffix, ConvertFormat.PDF)])
    assert body['result']['content'] == _pages(*PIXELS, unit=unit)


def test_page_images_follow_the_adapter_resolution(
    client: TestClient, auth_headers: dict[str, str], install: Install, monkeypatch: pytest.MonkeyPatch
):
    adapter = FakeAdapter(answer=image_size)
    monkeypatch.setattr(FakeAdapter, 'dpi', 100)
    install(adapter)

    body = _post(client, auth_headers, 'scan.pdf', samples.pdf_pages(SIZES[0])).json()

    assert body['result']['content'] == _pages('100x100')


# - - - Fallback to text


def test_formats_without_pages_fall_back_to_text(
    client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter
):
    body = _post(client, auth_headers, 'data.csv', CSV).json()

    assert '| beta | 2 |' in body['result']['content']
    assert body['options'] == {'readMode': 'text'}
    assert adapter.calls == 0


def test_spreadsheets_fall_back_to_text(client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter):
    body = _post(client, auth_headers, 'book.xlsx', samples.xlsx()).json()

    assert 'region' in body['result']['content'] and body['options'] == {'readMode': 'text'}


def test_unsupported_format_is_rejected(client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter):
    response = _post(client, auth_headers, 'blob.dat', BINARY)

    assert (response.status_code, response.json()['code']) == (415, 'unsupported_format')


# - - - Auto


SCAN = samples.pdf_streams(samples.PDF_IMAGE)


def _auto(client: TestClient, headers: dict[str, str], name: str, content: bytes):
    """No readMode: auto is the default"""
    return client.post('/v1/markdown', headers=headers, files={'file': (name, content)})


def test_auto_reads_scans_with_ocr(client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter):
    response = _auto(client, auth_headers, 'scan.pdf', SCAN)

    body = response.json()
    assert response.status_code == 200, response.text
    assert body['options'] == {'readMode': 'ocr'}
    assert body['result']['content'] == _pages('556x556')
    assert adapter.calls == 1


def test_auto_reads_native_text(client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter):
    body = _auto(client, auth_headers, 'a.pdf', samples.pdf()).json()

    assert 'Hello PDF' in body['result']['content']
    assert body['options'] == {'readMode': 'text'}
    assert adapter.calls == 0


def test_auto_without_backend_reads_text(client: TestClient, auth_headers: dict[str, str]):
    response = _auto(client, auth_headers, 'scan.pdf', SCAN)

    assert response.status_code == 200, response.text
    assert response.json()['options'] == {'readMode': 'text'}


def test_auto_does_not_hide_backend_failures(client: TestClient, auth_headers: dict[str, str], install: Install):
    install(FakeAdapter(OcrRejected('the OCR backend answered HTTP 400')))

    response = _auto(client, auth_headers, 'scan.pdf', SCAN)

    assert (response.status_code, response.json()['code']) == (502, 'ocr_failed')


def test_auto_reads_long_documents_as_text(
    client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter, caplog: pytest.LogCaptureFixture
):
    content = samples.pdf_streams(*[samples.PDF_IMAGE] * (MAX_PAGES + 1))

    with caplog.at_level('INFO', logger='papyrus.markdown'):
        response = _auto(client, auth_headers, 'long.pdf', content)

    assert response.status_code == 200, response.text
    assert response.json()['options'] == {'readMode': 'text'}
    assert adapter.calls == 0
    assert 'read as text' in caplog.text


@pytest.mark.parametrize(
    ('name', 'content'), [('photo.png', samples.image('PNG', (64, 48))), ('deck.pptx', samples.pptx())]
)
def test_auto_reads_images_and_presentations_with_ocr(
    client: TestClient,
    auth_headers: dict[str, str],
    adapter: FakeAdapter,
    convert: FakePdfConvert,
    name: str,
    content: bytes,
):
    body = _auto(client, auth_headers, name, content).json()

    assert body['options'] == {'readMode': 'ocr'}


def test_auto_classifies_legacy_documents_once_modernized(
    client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter, convert: FakePdfConvert
):
    body = _auto(client, auth_headers, 'report.odt', samples.odt()).json()

    assert body['options'] == {'readMode': 'text'}
    assert body['result']['content'].startswith('# Report')
    assert body['source']['label'] == 'odt'
    assert convert.calls == [('.odt', ConvertFormat.DOCX)]
    assert adapter.calls == 0


def test_auto_reads_spreadsheets_as_text(client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter):
    body = _auto(client, auth_headers, 'book.xlsx', samples.xlsx()).json()

    assert body['options'] == {'readMode': 'text'}


# - - - Outputs


def test_file_output_is_markdown(client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter):
    response = _post(client, auth_headers, 'scan.pdf', samples.pdf_pages(*SIZES), output='file')

    assert response.headers['content-type'] == 'text/markdown; charset=utf-8'
    assert response.text == _pages(*PIXELS)


def test_destination_receives_markdown(
    client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter, memory_fs: AbstractFileSystem
):
    response = _post(
        client, auth_headers, 'scan.pdf', samples.pdf_pages(*SIZES), destination='memory://mem/out/scan.md'
    )

    assert 'content' not in response.json()['result']
    assert response.json()['source']['pageCount'] == 3
    assert memory_fs.cat('out/scan.md').decode() == _pages(*PIXELS)


@pytest.mark.usefixtures('chunking_assets')
def test_chunks_of_slides_carry_their_number(
    client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter, convert: FakePdfConvert
):
    body = _post(client, auth_headers, 'deck.pptx', samples.pptx(), {'chunking': {'maxTokens': 64}}).json()

    assert [chunk['slide'] for chunk in body['chunks']] == [1, 2, 3]
    assert all('page' not in chunk for chunk in body['chunks'])
    assert all(body['result']['content'][chunk['start'] : chunk['end']] == chunk['text'] for chunk in body['chunks'])


@pytest.mark.usefixtures('chunking_assets')
def test_chunks_of_pages_carry_their_number(client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter):
    adapter.answer = lambda image: f'# {image_size(image)}\n\nBody.'
    options = {'chunking': {'maxTokens': 64}}

    body = _post(client, auth_headers, 'scan.pdf', samples.pdf_pages(*SIZES), options).json()

    assert [(chunk['page'], chunk['headings']) for chunk in body['chunks']] == [
        (number, [pixels]) for number, pixels in enumerate(PIXELS, start=1)
    ]
    assert all('slide' not in chunk for chunk in body['chunks'])


# - - - Errors


def test_unconfigured_backend_is_rejected(client: TestClient, auth_headers: dict[str, str], work_dir: Path):
    response = _post(client, auth_headers, 'data.csv', CSV)

    assert (response.status_code, response.json()['code']) == (422, 'ocr_not_configured')
    assert _leftovers(work_dir) == []


@pytest.mark.parametrize(
    ('name', 'content'),
    [
        ('long.pdf', samples.pdf_pages(*[SIZES[0]] * (MAX_PAGES + 1))),
        ('long.tiff', samples.image('TIFF', frames=MAX_PAGES + 1)),
    ],
)
def test_too_many_pages_never_reach_the_backend(
    client: TestClient, auth_headers: dict[str, str], adapter: FakeAdapter, name: str, content: bytes
):
    response = _post(client, auth_headers, name, content)

    assert (response.status_code, response.json()['code']) == (413, 'too_many_pages')
    assert str(MAX_PAGES + 1) in response.json()['detail']
    assert adapter.calls == 0


def test_one_failed_page_fails_the_request(
    client: TestClient, auth_headers: dict[str, str], install: Install, work_dir: Path
):
    def answer(image: Path) -> str:
        if image_size(image) == PIXELS[1]:
            raise OcrRejected('the OCR backend answered HTTP 400')
        return 'text'

    install(FakeAdapter(answer=answer))
    response = _post(client, auth_headers, 'scan.pdf', samples.pdf_pages(*SIZES))

    assert (response.status_code, response.json()['code']) == (502, 'ocr_failed')
    assert response.json()['detail'] == 'page 2: the OCR backend answered HTTP 400'
    assert _leftovers(work_dir) == []


def test_rate_limit_asks_to_retry_later(client: TestClient, auth_headers: dict[str, str], install: Install):
    install(FakeAdapter(OcrRateLimited('busy', 60)))

    response = _post(client, auth_headers, 'photo.png', samples.image('PNG'))

    assert (response.status_code, response.json()['code']) == (503, 'ocr_rate_limited')
    assert response.headers['retry-after'] == '30'


@pytest.mark.parametrize(
    ('name', 'content', 'status', 'code'),
    [
        ('huge.png', samples.png_header(20_000, 20_000), 413, 'image_too_large'),
        ('broken.pdf', samples.pdf()[:60] + bytes(2000), 415, 'unsupported_format'),
        ('empty.pdf', samples.pdf_pages(), 415, 'unsupported_format'),
    ],
)
def test_unreadable_sources(
    client: TestClient,
    auth_headers: dict[str, str],
    adapter: FakeAdapter,
    name: str,
    content: bytes,
    status: int,
    code: str,
):
    response = _post(client, auth_headers, name, content)

    assert (response.status_code, response.json()['code']) == (status, code)
    assert adapter.calls == 0


def test_rejects_unknown_read_mode(client: TestClient, auth_headers: dict[str, str]):
    response = _post(client, auth_headers, 'data.csv', CSV, {'readMode': 'fast'})

    assert response.status_code == 422
    assert response.json()['errors'][0]['loc'] == ['options', 'readMode']


@pytest.mark.full
def test_office_documents_render_with_libreoffice(
    client: TestClient, app: FastAPI, auth_headers: dict[str, str], adapter: FakeAdapter
):
    docx = _post(client, auth_headers, 'report.docx', samples.docx_pages(3)).json()
    pptx = _post(client, auth_headers, 'deck.pptx', samples.pptx_slides(2)).json()

    assert docx['source']['pageCount'] == 3 and docx['result']['content'].count('Page number') == 3
    assert pptx['source']['pageCount'] == 2 and pptx['result']['content'].count('Slide number') == 2
