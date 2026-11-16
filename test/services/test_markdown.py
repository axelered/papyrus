from collections.abc import Iterator
from pathlib import Path

import pytest

from server.errors import UnsupportedFormat
from server.services.markdown import MarkdownService, decode_text, fence
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


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('x = 1\n', '```python\nx = 1\n```\n'),
        ('x = 1', '```python\nx = 1\n```\n'),
        ('', '```python\n```\n'),
        ('s = """\n```\n"""\n', '````python\ns = """\n```\n"""\n````\n'),
        ('a = "`````"\n', '``````python\na = "`````"\n``````\n'),
    ],
)
def test_fence_cannot_be_broken_out_of(text: str, expected: str):
    assert fence(text, 'python') == expected


@pytest.mark.parametrize(
    ('raw', 'text'),
    [
        (b'', ''),
        ('x = "café"\n'.encode(), 'x = "café"\n'),
        ('﻿x = 1\n'.encode(), 'x = 1\n'),
        ('x = "日本語のテキスト"\n'.encode('utf-16'), 'x = "日本語のテキスト"\n'),
    ],
)
def test_decode_text_detects_charset(raw: bytes, text: str):
    assert decode_text(raw) == text


async def test_fences_code_source(service: MarkdownService, make_document: DocumentFactory, tmp_path: Path):
    target = tmp_path / 'out.md'

    await service.fence_code(make_document('loader.py', b'# setup\r\nimport os\r\n'), target, 'python')

    assert target.read_bytes() == b'```python\n# setup\nimport os\n```\n'
