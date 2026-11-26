"""The sync client against the server under test, relying only on the defaults of a clean server"""

import io
import subprocess
import sys
from pathlib import Path

import pytest
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
)
from support import BINARY, CSV, HTML, JPEG, PNG, RTF, WEBP, pdf

PAGE = (200, 100)


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


def test_markdown_chunks(sdk: Papyrus):
    chunking = ChunkingOptions(max_tokens=64, tokenizer=Tokenizer.WORD)

    response = sdk.markdown(b'# Title\n\nSome prose.\n', filename='a.md', chunking=chunking)

    assert response.options.chunking == ChunkingOptions(max_tokens=64, overlap=0, tokenizer=Tokenizer.WORD)
    assert response.chunks and response.chunks[0].kind is ChunkKind.TEXT
    chunk = response.chunks[0]
    assert response.result.text[chunk.start : chunk.end] in chunk.text


def test_markdown_through_libreoffice(sdk: Papyrus):
    assert 'Hello from rtf' in sdk.markdown(RTF, filename='a.rtf').result.text


def test_markdown_of_a_non_ascii_name(sdk: Papyrus):
    response = sdk.markdown(CSV, filename='prezzi è.csv')

    assert (response.source.name, response.source.label) == ('prezzi è.csv', 'csv')
    assert (response.result.name, response.options.read_mode) == ('prezzi è.md', ReadMode.TEXT)


def test_markdown_ocr_without_a_backend(sdk: Papyrus):
    with pytest.raises(APIError) as error:
        sdk.markdown(pdf(PAGE), filename='scan.pdf', read_mode='ocr')

    assert (error.value.status, error.value.code) == (422, 'ocr_not_configured')


def test_every_upload_form_sends_the_same_document(sdk: Papyrus, tmp_path: Path):
    path = tmp_path / 'data.csv'
    path.write_bytes(CSV)
    partly_read = io.BytesIO(CSV)
    partly_read.read(5)
    # a pipe, not a file: no size, no seek; the interpreter is the only program every host has
    copy = 'import shutil, sys; shutil.copyfileobj(open(sys.argv[1], "rb"), sys.stdout.buffer)'
    process = subprocess.Popen([sys.executable, '-c', copy, str(path)], stdout=subprocess.PIPE)

    with path.open('rb') as opened, process:
        responses = [
            sdk.inspect(path),
            sdk.inspect(str(path)),
            sdk.inspect(opened),
            sdk.inspect(CSV, filename='data.csv'),
            sdk.inspect(file=str(path)),
            sdk.inspect(file=path),
            sdk.inspect(file=partly_read, filename='data.csv'),
            sdk.inspect(file=process.stdout, filename='data.csv'),
        ]

    assert {(r.source.name, r.source.size, r.source.label) for r in responses} == {('data.csv', len(CSV), 'csv')}


def test_render(sdk: Papyrus):
    response = sdk.render(pdf(PAGE, PAGE), filename='report.pdf', page=2, width=400, format='webp')

    assert (response.result.name, response.source.page_count) == ('report-2.webp', 2)
    assert (response.options.page, response.options.format, response.options.dpi) == (2, ImageFormat.WEBP, None)
    assert (response.result.width, response.result.height) == (400, 200)
    assert response.result.data.startswith(WEBP) and len(response.result.data) == response.result.size


def test_render_options_left_out_take_the_server_defaults(sdk: Papyrus):
    options = sdk.render(pdf(PAGE)).options

    assert (options.page, options.dpi, options.format) == (1, 150, ImageFormat.PNG)


def test_thumbnail(sdk: Papyrus, tmp_path: Path):
    response = sdk.thumbnail(pdf(PAGE), width=64, height=64, fit=Fit.COVER, format=ImageFormat.JPEG)

    assert response.result.encoding is Encoding.BASE64
    assert response.options.fit is Fit.COVER
    assert (response.result.width, response.result.height) == (64, 64)
    assert response.result.save(tmp_path / 'a.jpg').read_bytes().startswith(JPEG)


def test_thumbnail_through_chromium(sdk: Papyrus):
    assert sdk.thumbnail(HTML, filename='a.html', format='png').result.data.startswith(PNG)


def test_inspect(sdk: Papyrus):
    response = sdk.inspect(CSV, filename='wrong.pdf')

    assert response.checks.extension_match is False


def test_problems_become_api_errors(sdk: Papyrus):
    with pytest.raises(APIError) as unsupported:
        sdk.markdown(BINARY, filename='a.bin')
    with pytest.raises(APIError) as invalid:
        sdk.render(pdf(PAGE), page=0)
    with pytest.raises(APIError) as out_of_range:
        sdk.render(pdf(PAGE), page=9)
    with pytest.raises(APIError) as unknown:
        sdk.markdown(source='s3://nope/a.pdf')

    assert (unsupported.value.status, unsupported.value.code) == (415, 'unsupported_format')
    assert unsupported.value.detail and unsupported.value.request_id
    assert (invalid.value.code, invalid.value.errors[0].loc) == ('validation_error', ['options', 'page'])
    assert (out_of_range.value.status, out_of_range.value.code) == (422, 'page_out_of_range')
    assert unknown.value.code == 'unknown_connector'


@pytest.mark.parametrize('operation', ['markdown', 'screenshot'])
def test_private_addresses_are_refused(sdk: Papyrus, operation: str):
    with pytest.raises(APIError) as error:
        getattr(sdk, operation)('http://127.0.0.1:8000/')

    assert (error.value.status, error.value.code) == (403, 'source_forbidden')


def test_source_never_reads_the_disk(sdk: Papyrus, tmp_path: Path):
    secret = tmp_path / 'secret.txt'
    secret.write_text('token')

    for uri in (str(secret), secret.as_uri(), 'file://file'):
        with pytest.raises(APIError) as error:
            sdk.markdown(source=uri)
        assert error.value.code == 'unsupported_uri'


def test_wrong_key_is_unauthorized(server: tuple[str, str]):
    with pytest.raises(APIError) as error, Papyrus(server[0], f'{server[1]}x') as client:
        client.inspect(CSV)

    assert (error.value.status, error.value.code) == (401, 'unauthorized')


def test_probes(sdk: Papyrus):
    assert sdk.health().status == 'ok'
    assert sdk.ready() is True
