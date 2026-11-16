from pathlib import Path

import pytest

from server.errors import ProcessingFailed
from server.formats import Formats, lookup
from server.services.detect import DetectService, normalize_extension
from test.fixtures.documents import DocumentFactory
from test.fixtures.samples import HTML, PYTHON, SAMPLES

pytestmark = pytest.mark.anyio


@pytest.fixture(scope='module')
def service() -> DetectService:
    return DetectService()


@pytest.mark.parametrize('label', sorted(SAMPLES))
async def test_pins_magika_label_per_fixture(service: DetectService, make_document: DocumentFactory, label: str):
    document = await service.detect(make_document('sample', SAMPLES[label]()))

    assert document.format.label == label


async def test_content_wins_and_extension_is_normalized(service: DetectService, make_document: DocumentFactory):
    document = await service.detect(make_document('report.pdf', HTML))

    assert document.format is Formats.HTML
    assert document.path.name == 'report.html'
    assert document.path.exists()
    assert not document.path.with_name('report.pdf').exists()


async def test_extension_refines_generic_content(service: DetectService, make_document: DocumentFactory):
    document = await service.detect(make_document('feed.rss', b'<?xml version="1.0"?><rss><channel/></rss>'))

    assert document.format is Formats.RSS
    assert document.path.name == 'feed.rss'


async def test_derived_code_format(service: DetectService, make_document: DocumentFactory):
    document = await service.detect(make_document('loader', PYTHON))

    assert document.format == lookup('python')
    assert document.path.name == 'loader.py'


async def test_empty_file_uses_extension(service: DetectService, make_document: DocumentFactory):
    document = await service.detect(make_document('empty.txt', b''))

    assert document.format is Formats.TXT


async def test_unknown_content_keeps_name(service: DetectService, make_document: DocumentFactory):
    import random

    document = await service.detect(make_document('blob.weird', random.Random(0).randbytes(512)))

    assert document.format is Formats.UNKNOWN
    assert document.path.name == 'blob.weird'


async def test_wraps_detection_errors(service: DetectService, tmp_path: Path):
    with pytest.raises(ProcessingFailed):
        await service.detect(tmp_path / 'missing.pdf')


def test_normalize_keeps_matching_alias(tmp_path: Path):
    path = tmp_path / 'page.HTM'
    path.write_text('x')

    assert normalize_extension(path, Formats.HTML) == path
