from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from server.errors import UnsupportedFormat
from server.services.markdown import MarkdownService
from test.fixtures.documents import BINARY, CSV, HTML, DocumentFactory

pytestmark = pytest.mark.anyio


@pytest.fixture(scope='module')
async def service() -> AsyncIterator[MarkdownService]:
    async with MarkdownService(processes=1) as service:
        yield service


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


async def test_fences_code_source(service: MarkdownService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.md'

    await service.fence_code(make_document('loader.py', b'# setup\r\nimport os\r\n'), target, 'python')

    assert target.read_bytes() == b'```python\n# setup\nimport os\n```\n'


async def test_warm_starts_initialized_workers(make_document: DocumentFactory, tmp_path: Path):
    async with MarkdownService(processes=1) as warm:
        await warm.warm()

        assert warm._pool.started
        assert await warm.convert(make_document('report.html', HTML), tmp_path / 'out.md') == 'Quarterly'
