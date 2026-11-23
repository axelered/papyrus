from collections import Counter
from pathlib import Path

import pytest

from server.errors import ProcessingFailed
from server.services.detect import (
    EXPLICIT,
    DetectService,
    Formats,
    _brand,
    extension_match,
    from_extension,
    lookup,
    normalize_extension,
    resolve,
)
from server.types.common import FormatGroup, FormatLabel
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


async def test_avif_is_told_apart_from_mp4_by_brand(service: DetectService, make_document: DocumentFactory):
    document = await service.detect(make_document('photo.dat', SAMPLES['avif']()))

    assert document.format.label == FormatLabel.AVIF
    assert document.path.name == 'photo.avif'


def test_brand_requires_an_ftyp_box(tmp_path: Path):
    path = tmp_path / 'x'
    path.write_bytes(b'\x00\x00\x00\x1cftypavif')
    assert _brand(path) == b'avif'
    path.write_bytes(b'not a container')
    assert _brand(path) == b''


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


async def test_tiny_text_trusts_its_extension(service: DetectService, make_document: DocumentFactory):
    document = await service.detect(make_document('p.html', b'<h1>T</h1><p>x</p>'))

    assert document.format is Formats.HTML
    assert document.path.name == 'p.html'


_NOT_MAGIKA_LABELS = {FormatLabel.RSS, FormatLabel.ATOM}


def test_every_label_has_one_explicit_format():
    assert set(EXPLICIT) == set(FormatLabel)
    assert all(EXPLICIT[label].label is label for label in FormatLabel)


def test_explicit_extensions_and_mimes_are_unique():
    extensions = Counter(ext for value in EXPLICIT.values() for ext in value.extensions)
    mimes = Counter(value.mime_type for value in EXPLICIT.values())

    assert [ext for ext, count in extensions.items() if count > 1] == []
    assert [mime for mime, count in mimes.items() if count > 1] == []


def test_explicit_labels_match_magika_labels():
    from server.services.detect import _magika_kb

    missing = {label for label in FormatLabel if label not in _magika_kb()} - _NOT_MAGIKA_LABELS

    assert missing == set()


def test_bases_resolve_to_explicit_formats():
    assert all(value.base in EXPLICIT for value in EXPLICIT.values() if value.base)


def test_lookup_explicit():
    assert lookup('pdf') is Formats.PDF


def test_lookup_derives_text_formats_from_magika():
    python = lookup('python')

    assert (python.mime_type, python.extension, python.group) == ('text/x-python', 'py', FormatGroup.CODE)
    assert python.text and python.derived
    assert python.base is FormatLabel.TXT
    assert lookup('latex').group is FormatGroup.TEXT


def test_lookup_derives_binary_formats_as_unknown_group():
    mp4 = lookup('mp4')

    assert (mp4.mime_type, mp4.group, mp4.text, mp4.derived) == ('video/mp4', FormatGroup.UNKNOWN, False, True)


def test_lookup_images_are_explicit():
    assert (lookup('png').group, lookup('png').derived) == (FormatGroup.IMAGE, False)
    assert lookup('jpeg').extensions == ('jpg', 'jpeg', 'jpe')


def test_lookup_unknown_label():
    assert lookup('not-a-label') is Formats.UNKNOWN


@pytest.mark.parametrize(
    ('extension', 'label'),
    [('PDF', 'pdf'), ('.htm', 'html'), ('md', 'markdown'), ('xlsm', 'xlsx'), ('py', 'python'), ('ts', 'typescript')],
)
def test_from_extension(extension: str, label: str):
    assert from_extension(extension).label == label


@pytest.mark.parametrize('extension', ['', 'nope', 'bin'])
def test_from_extension_unknown(extension: str):
    assert from_extension(extension) is None


@pytest.mark.parametrize(
    ('detected', 'extension', 'label'),
    [
        ('txt', 'csv', 'csv'),
        ('txt', 'py', 'python'),
        ('xml', 'rss', 'rss'),
        ('txt', 'atom', 'atom'),
        ('zip', 'docx', 'docx'),
        ('json', 'ipynb', 'ipynb'),
        ('html', 'pdf', 'html'),
        ('erb', 'html', 'html'),
        ('javascript', 'py', 'python'),
        ('typescript', 'js', 'javascript'),
        ('python', 'csv', 'csv'),
        ('html', 'png', 'html'),
        ('png', 'html', 'png'),
        ('pdf', '', 'pdf'),
        ('pdf', 'docx', 'pdf'),
        ('python', 'txt', 'python'),
        ('json', 'jsonl', 'jsonl'),
        ('jsonl', 'json', 'jsonl'),
        ('empty', 'txt', 'txt'),
        ('undefined', 'md', 'markdown'),
        ('empty', '', 'unknown'),
        ('unknown', 'csv', 'csv'),
        ('unknown', 'nope', 'unknown'),
        ('unknown', '', 'unknown'),
    ],
)
def test_resolve(detected: str, extension: str, label: str):
    assert resolve(detected, extension).label == label


@pytest.mark.parametrize(
    ('label', 'extension', 'match'),
    [
        ('html', 'html', True),
        ('html', '.HTM', True),
        ('jpeg', 'jpeg', True),
        ('python', 'txt', True),
        ('docx', 'zip', True),
        ('jsonl', 'json', True),
        ('unknown', 'bin', True),
        ('html', 'pdf', False),
        ('txt', 'csv', False),
        ('html', 'nope', False),
        ('html', 'bin', False),
        ('html', '', None),
    ],
)
def test_extension_match(label: str, extension: str, match: bool | None):
    assert extension_match(lookup(label), extension) is match
