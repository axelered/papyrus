from collections.abc import Iterator
from pathlib import Path

import pytest

from server.errors import UnsupportedFormat
from server.services.markdown import MarkdownService
from test.fixtures.documents import BINARY, CSV, HTML, DocumentFactory

pytestmark = pytest.mark.anyio


@pytest.fixture(scope='module')
def service() -> Iterator[MarkdownService]:
    service = MarkdownService(processes=1)
    yield service
    service.close()


async def test_converts_html_with_title(service: MarkdownService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.md'

    title = await service.convert(make_document('report.html', HTML), target)

    assert title == 'Quarterly'
    assert target.read_text(encoding='utf-8').strip() == '# Report\n\nRevenue grew.'


async def test_converts_csv_to_table(service: MarkdownService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.md'

    assert await service.convert(make_document('data.csv', CSV), target) is None
    assert '| alpha | 1 |' in target.read_text(encoding='utf-8')


async def test_rejects_unsupported_binary(service: MarkdownService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.md'

    with pytest.raises(UnsupportedFormat):
        await service.convert(make_document('blob.dat', BINARY), target)

    assert not target.exists()
