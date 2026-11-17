import threading
import time
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from server.errors import ProcessingTimeout, UnsupportedFormat
from server.services.convert import ConvertService
from server.types.convert import ConvertFormat
from test.fixtures import samples
from test.fixtures.documents import BINARY, DocumentFactory

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.full,
]


@pytest.fixture(scope='module')
def service(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ConvertService]:
    service = ConvertService(processes=1, profiles=tmp_path_factory.mktemp('work') / 'profiles')
    yield service
    service.close()


@pytest.fixture
def requests_seen() -> Iterator[tuple[str, list[str]]]:
    """Local HTTP server that records every path LibreOffice asks for"""
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', seen
    server.shutdown()
    server.server_close()


def _entry(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name).decode('utf-8')


@pytest.mark.parametrize(
    ('key', 'format', 'entry', 'text'),
    [
        ('doc', ConvertFormat.DOCX, 'word/document.xml', 'Revenue grew.'),
        ('ppt', ConvertFormat.PPTX, 'ppt/slides/slide1.xml', 'Roadmap'),
        ('xls', ConvertFormat.XLSX, 'xl/sharedStrings.xml', 'region'),
    ],
)
async def test_converts_legacy_binaries_to_ooxml(
    service: ConvertService,
    legacy: dict[str, Path],
    tmp_path: Path,
    key: str,
    format: ConvertFormat,
    entry: str,
    text: str,
):
    target = tmp_path / f'out.{format}'

    await service.convert(legacy[key], target, format)

    assert text in _entry(target, entry)


async def test_converts_odt_keeping_headings(service: ConvertService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.docx'

    await service.convert(make_document('report.odt', samples.odt()), target, ConvertFormat.DOCX)

    document = _entry(target, 'word/document.xml')
    assert 'Revenue grew.' in document
    assert 'w:val="Heading1"' in document


async def test_converts_rtf_to_docx(service: ConvertService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.docx'

    await service.convert(make_document('report.rtf', samples.RTF), target, ConvertFormat.DOCX)

    assert 'Revenue grew.' in _entry(target, 'word/document.xml')


async def test_converts_to_pdf(service: ConvertService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.pdf'

    await service.convert(make_document('report.odt', samples.odt()), target, ConvertFormat.PDF)

    assert target.read_bytes().startswith(b'%PDF-')


async def test_rejects_truncated_legacy_input(
    service: ConvertService, legacy: dict[str, Path], make_document: DocumentFactory, tmp_path: Path
):
    content = legacy['doc'].read_bytes()
    target = tmp_path / 'out.docx'

    with pytest.raises(UnsupportedFormat):
        await service.convert(make_document('broken.doc', content[: len(content) // 2]), target, ConvertFormat.DOCX)

    assert not target.exists()


async def test_imports_unrecognized_bytes_as_text(
    service: ConvertService, make_document: DocumentFactory, tmp_path: Path
):
    """LibreOffice falls back to its text filter; routing only sends magika-detected legacy formats here"""
    target = tmp_path / 'out.docx'

    await service.convert(make_document('random.doc', BINARY), target, ConvertFormat.DOCX)

    assert target.is_file()


async def test_rejects_encrypted_input_without_hanging(
    service: ConvertService, legacy: dict[str, Path], tmp_path: Path
):
    started = time.monotonic()

    with pytest.raises(UnsupportedFormat):
        await service.convert(legacy['encrypted'], tmp_path / 'out.docx', ConvertFormat.DOCX)

    assert time.monotonic() - started < 10


async def test_rejects_target_without_export_filter(
    service: ConvertService, make_document: DocumentFactory, tmp_path: Path
):
    with pytest.raises(UnsupportedFormat, match='cannot be converted to pptx'):
        await service.convert(make_document('sheet.xlsx', samples.xlsx()), tmp_path / 'out.pptx', ConvertFormat.PPTX)


async def test_does_not_fetch_remote_images(
    service: ConvertService, make_document: DocumentFactory, tmp_path: Path, requests_seen: tuple[str, list[str]]
):
    base, seen = requests_seen
    source = make_document(
        'linked.odt', samples.odt(samples.ODT_REPORT + samples.odt_linked_image(f'{base}/pixel.png'))
    )

    await service.convert(source, tmp_path / 'out.pdf', ConvertFormat.PDF)
    await service.convert(source, tmp_path / 'out.docx', ConvertFormat.DOCX)

    assert seen == []


async def test_does_not_embed_local_files(service: ConvertService, make_document: DocumentFactory, tmp_path: Path):
    secret = tmp_path / 'secret.txt'
    secret.write_text('TOPSECRET', encoding='utf-8')
    source = make_document('leak.odt', samples.odt(samples.odt_linked_section(secret.as_uri())))
    target = tmp_path / 'out.docx'

    await service.convert(source, target, ConvertFormat.DOCX)

    assert 'TOPSECRET' not in _entry(target, 'word/document.xml')


async def test_recycles_libreoffice_in_fresh_workers(make_document: DocumentFactory, tmp_path: Path):
    service = ConvertService(processes=1, profiles=tmp_path / 'profiles', max_tasks=1)
    source = make_document('report.odt', samples.odt())
    try:
        for index in range(2):
            await service.convert(source, tmp_path / f'out{index}.docx', ConvertFormat.DOCX)
    finally:
        service.close()

    assert (tmp_path / 'out1.docx').is_file()
    assert not (tmp_path / 'profiles').exists()


async def test_timeout_kills_worker_and_next_worker_converts(make_document: DocumentFactory, tmp_path: Path):
    profiles = tmp_path / 'profiles'
    service = ConvertService(processes=1, profiles=profiles, timeout=0.2)
    source = make_document('report.odt', samples.odt())
    try:
        with pytest.raises(ProcessingTimeout):
            await service.convert(source, tmp_path / 'slow.docx', ConvertFormat.DOCX)
        service._pool.timeout = 120

        await service.convert(source, tmp_path / 'out.docx', ConvertFormat.DOCX)

        assert (tmp_path / 'out.docx').is_file()
        assert len(list(profiles.iterdir())) == 1
        assert list(source.parent.glob('.~lock*')) == []
    finally:
        service.close()
