"""The sync client against the real app"""

import io
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fsspec import AbstractFileSystem
from papyrus_sdk import (
    APIError,
    ChunkingOptions,
    ChunkKind,
    Encoding,
    Fit,
    FormatGroup,
    ImageFormat,
    Papyrus,
    ReadMode,
    Tokenizer,
    WaitUntil,
)
from PIL import Image

from server.config import Config
from server.services.ocr import OcrService
from test.fixtures.app import API_KEY
from test.fixtures.browser import PNG, FakeBrowser
from test.fixtures.documents import BINARY, CSV, HTML
from test.fixtures.ocr import FakeAdapter
from test.fixtures.samples import pdf_pages
from test.fixtures.sdk import BASE_URL

A4 = (595, 842)


@pytest.fixture
def ocr_adapter(client: TestClient) -> FakeAdapter:
    adapter = FakeAdapter()
    config = Config.model_validate({'ocr': {'type': 'qwen', 'model': 'm', 'url': 'http://vllm/v1'}}).ocr
    client.app.state.services.ocr = OcrService(config, renders=1, adapter=adapter, backoff=0)
    return adapter


def test_markdown_upload(sdk: Papyrus, tmp_path: Path):
    path = tmp_path / 'report.html'
    path.write_bytes(HTML)

    response = sdk.markdown(path)

    assert response.result.text.startswith('# Report')
    assert (response.result.name, response.result.encoding) == ('report.md', Encoding.UTF8)
    assert (response.source.uri, response.source.name) == ('file://file', 'report.html')
    assert response.source.group is FormatGroup.MARKUP
    assert response.source.title == 'Quarterly'
    assert (response.options.read_mode, response.options.chunking) == (ReadMode.TEXT, None)
    assert response.chunks is None
    assert response.request_id


@pytest.mark.usefixtures('chunking_assets')
def test_markdown_chunks(sdk: Papyrus):
    chunking = ChunkingOptions(max_tokens=64, tokenizer=Tokenizer.WORD)

    response = sdk.markdown(b'# Title\n\nSome prose.\n', filename='a.md', chunking=chunking)

    assert response.options.chunking == ChunkingOptions(max_tokens=64, overlap=0, tokenizer=Tokenizer.WORD)
    assert response.chunks and response.chunks[0].kind is ChunkKind.TEXT
    chunk = response.chunks[0]
    assert response.result.text[chunk.start : chunk.end] in chunk.text


@pytest.mark.usefixtures('chunking_assets')
def test_markdown_ocr(sdk: Papyrus, ocr_adapter: FakeAdapter):
    response = sdk.markdown(pdf_pages(A4, A4), filename='scan.pdf', read_mode='ocr', chunking=ChunkingOptions())

    assert response.options.read_mode is ReadMode.OCR
    assert response.source.page_count == ocr_adapter.calls == 2
    assert response.chunks and {chunk.page for chunk in response.chunks} == {1}


def test_markdown_ocr_falls_back_on_formats_without_pages(sdk: Papyrus, ocr_adapter: FakeAdapter):
    response = sdk.markdown(CSV, filename='data.csv', read_mode=ReadMode.OCR)

    assert (response.options.read_mode, ocr_adapter.calls) == (ReadMode.TEXT, 0)


def test_markdown_ocr_without_a_backend(sdk: Papyrus):
    with pytest.raises(APIError) as error:
        sdk.markdown(pdf_pages(A4), filename='scan.pdf', read_mode='ocr')

    assert (error.value.status, error.value.code) == (422, 'ocr_not_configured')


def test_every_upload_form_sends_the_same_document(sdk: Papyrus, tmp_path: Path):
    path = tmp_path / 'data.csv'
    path.write_bytes(CSV)
    partly_read = io.BytesIO(CSV)
    partly_read.read(5)
    pipe = subprocess.Popen(['cat', str(path)], stdout=subprocess.PIPE).stdout

    with path.open('rb') as opened:
        responses = [
            sdk.inspect(path),
            sdk.inspect(str(path)),
            sdk.inspect(opened),
            sdk.inspect(CSV, filename='data.csv'),
            sdk.inspect(file=str(path)),
            sdk.inspect(file=path),
            sdk.inspect(file=partly_read, filename='data.csv'),
            sdk.inspect(file=pipe, filename='data.csv'),
        ]

    assert {(r.source.name, r.source.size, r.source.label) for r in responses} == {('data.csv', len(CSV), 'csv')}


@pytest.mark.parametrize('form', ['positional', 'source'])
def test_connector_source_and_destination(sdk: Papyrus, memory_fs: AbstractFileSystem, form: str):
    memory_fs.pipe('bucket/in.html', HTML)
    uri, destination = 'memory://mem/bucket/in.html', 'memory://mem/bucket/out.md'

    response = (
        sdk.markdown(uri, destination=destination)
        if form == 'positional'
        else sdk.markdown(source=uri, destination=destination)
    )

    assert memory_fs.cat('bucket/out.md').startswith(b'# Report')
    assert (response.source.uri, response.result.uri) == (uri, destination)
    with pytest.raises(ValueError, match='memory://mem/bucket/out.md'):
        _ = response.result.text


def test_render(sdk: Papyrus):
    response = sdk.render(pdf_pages(A4, A4), filename='report.pdf', page=2, width=200, format='webp')

    assert (response.result.name, response.source.page_count) == ('report-2.webp', 2)
    assert (response.options.page, response.options.format, response.options.dpi) == (2, ImageFormat.WEBP, None)
    assert Image.open(io.BytesIO(response.result.data)).size == (response.result.width, response.result.height)


def test_render_options_left_out_take_the_server_defaults(sdk: Papyrus):
    options = sdk.render(pdf_pages(A4)).options

    assert (options.page, options.dpi, options.format) == (1, 150, ImageFormat.PNG)


def test_thumbnail(sdk: Papyrus, tmp_path: Path):
    response = sdk.thumbnail(pdf_pages(A4), width=64, height=64, fit=Fit.COVER, format=ImageFormat.JPEG)

    assert response.result.encoding is Encoding.BASE64
    assert response.options.fit is Fit.COVER
    assert Image.open(response.result.save(tmp_path / 'a.jpg')).size == (64, 64)


def test_screenshot(sdk: Papyrus, client: TestClient):
    fake = client.app.state.services.browser = FakeBrowser()

    response = sdk.screenshot('https://axelered.com/', full_page=True, wait_until='networkidle', delay=5)

    assert response.result.data == PNG
    assert response.source.fetch and response.source.fetch.status == 200
    assert (response.options.full_page, response.options.wait_until) == (True, WaitUntil.NETWORKIDLE)
    assert fake.calls[0][2].delay == 5


def test_inspect(sdk: Papyrus):
    response = sdk.inspect(CSV, filename='wrong.pdf')

    assert response.checks.extension_match is False


def test_problems_become_api_errors(sdk: Papyrus):
    with pytest.raises(APIError) as unsupported:
        sdk.markdown(BINARY, filename='a.bin')
    with pytest.raises(APIError) as invalid:
        sdk.render(pdf_pages(A4), page=0)
    with pytest.raises(APIError) as unknown:
        sdk.markdown(source='s3://nope/a.pdf')

    assert (unsupported.value.status, unsupported.value.code) == (415, 'unsupported_format')
    assert unsupported.value.detail and unsupported.value.request_id
    assert (invalid.value.code, invalid.value.errors[0].loc) == ('validation_error', ['options', 'page'])
    assert unknown.value.code == 'unknown_connector'


def test_source_never_reads_the_disk(sdk: Papyrus, tmp_path: Path):
    secret = tmp_path / 'secret.txt'
    secret.write_text('token')

    for uri in (str(secret), f'file://{secret}', 'file://file'):
        with pytest.raises(APIError) as error:
            sdk.markdown(source=uri)
        assert error.value.code == 'unsupported_uri'


def test_wrong_key_is_unauthorized(client: TestClient):
    with pytest.raises(APIError) as error:
        Papyrus(BASE_URL, f'{API_KEY}x', http_client=client).inspect(CSV)

    assert (error.value.status, error.value.code) == (401, 'unauthorized')


def test_queue_full_is_retried(sdk: Papyrus, app: FastAPI, sleeps: list[float], monkeypatch: pytest.MonkeyPatch):
    admission = app.state.admission
    admission.in_flight = admission.limit

    def free(delay: float) -> None:
        sleeps.append(delay)
        admission.in_flight = 0

    monkeypatch.setattr('papyrus_sdk.client.sleep', free)

    assert sdk.inspect(CSV, filename='data.csv').source.label == 'csv'
    assert len(sleeps) == 1 and 2 <= sleeps[0] <= 3


def test_probes(sdk: Papyrus, app: FastAPI, sleeps: list[float]):
    assert sdk.health().status == 'ok'
    assert sdk.ready() is True

    app.state.admission.in_flight = app.state.admission.limit

    assert sdk.health().status == 'ok'
    assert sdk.ready() is False
    assert sleeps == []
