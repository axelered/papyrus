import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from server.errors import UnsupportedFormat
from server.services.detect import Document, lookup
from server.services.markdown import MarkdownService
from test.fixtures.documents import BINARY, CSV, HTML, DocumentFactory
from test.fixtures.samples import PDF_IMAGE, pdf, pdf_streams

pytestmark = pytest.mark.anyio


@pytest.fixture(scope='module')
async def service() -> AsyncIterator[MarkdownService]:
    async with MarkdownService(processes=1) as service:
        yield service


def _document(path: Path, label: str) -> Document:
    return Document(path, lookup(label))


async def test_converts_html_with_title(service: MarkdownService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.md'

    result = await service.read(_document(make_document('report.html', HTML), 'html'), target)

    assert result.title == 'Quarterly'
    assert target.read_text(encoding='utf-8').strip() == '# Report\n\nRevenue grew.'


async def test_converts_csv_to_table(service: MarkdownService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.md'

    assert (await service.read(_document(make_document('data.csv', CSV), 'csv'), target)).title is None
    assert '| alpha | 1 |' in target.read_text(encoding='utf-8')


async def test_rejects_unsupported_binary(service: MarkdownService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.md'

    with pytest.raises(UnsupportedFormat):
        await service.read(_document(make_document('blob.dat', BINARY), 'unknown'), target)

    assert not target.exists()


async def test_fences_code_source(service: MarkdownService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.md'

    await service.read(_document(make_document('loader.py', b'# setup\r\nimport os\r\n'), 'python'), target)

    assert target.read_bytes() == b'```python\n# setup\nimport os\n```\n'


async def test_warm_starts_initialized_workers(make_document: DocumentFactory, tmp_path: Path):
    async with MarkdownService(processes=1) as warm:
        await warm.warm()

        assert warm._pool.started
        result = await warm.read(_document(make_document('report.html', HTML), 'html'), tmp_path / 'out.md')
        assert result.title == 'Quarterly'


@pytest.mark.parametrize(('content', 'needed'), [(pdf_streams(PDF_IMAGE), True), (pdf(), False)])
async def test_needs_ocr_classifies_in_the_pool(
    service: MarkdownService,
    make_document: DocumentFactory,
    caplog: pytest.LogCaptureFixture,
    content: bytes,
    needed: bool,
):
    document = Document(make_document('sample.pdf', content), lookup('pdf'))

    with caplog.at_level(logging.INFO, logger='papyrus.markdown'):
        assert await service.needs_ocr(document) is needed

    assert 'pdf classified as' in caplog.text
