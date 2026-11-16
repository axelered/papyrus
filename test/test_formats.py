from collections import Counter

import pytest

from server.formats import EXPLICIT, FormatGroup, FormatLabel, Formats, from_extension, lookup, resolve

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
    from server.formats import _magika_kb

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
    png = lookup('png')

    assert (png.mime_type, png.group, png.text, png.derived) == ('image/png', FormatGroup.UNKNOWN, False, True)


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
