import zipfile
from pathlib import Path

import pytest

from server.ocr.classify_docx import CHARS_PER_IMAGE, MIN_IMAGE_BYTES, classify_docx, docx_stats
from test.fixtures.samples import docx_media

CAPTION = 'Signed copy of the application'
# alphanumeric characters only, so the counts are exact
WORDS = 'Revenue grew in every region '


def _write(tmp_path: Path, text: str = '', media: dict[str, int] | None = None) -> Path:
    path = tmp_path / 'sample.docx'
    path.write_bytes(docx_media(text, media))
    return path


@pytest.mark.parametrize(
    ('text', 'media', 'expected'),
    [
        ('', None, 'empty'),
        ('', {'scan.png': 20 * 1024}, 'graphic'),  # a small scan without text
        (CAPTION, {'scan.jpeg': MIN_IMAGE_BYTES}, 'graphic'),  # a scan with a typed caption
        (WORDS * 40, {'chart.png': MIN_IMAGE_BYTES}, 'text'),  # a report with a screenshot
        (WORDS * 40, {'logo.png': MIN_IMAGE_BYTES - 1}, 'text'),  # logos do not count as content
        ('', {'drawing.emf': 100 * 1024}, 'empty'),  # vector drawings are not rasters
    ],
)
def test_classifies_documents(tmp_path: Path, text: str, media: dict[str, int] | None, expected: str):
    assert classify_docx(_write(tmp_path, text, media)) == expected


def test_text_per_content_image_settles_the_class(tmp_path: Path):
    media = {'a.png': MIN_IMAGE_BYTES, 'b.png': MIN_IMAGE_BYTES}
    below = 'x' * (CHARS_PER_IMAGE * 2 - 1)
    at = 'x' * (CHARS_PER_IMAGE * 2)

    assert classify_docx(_write(tmp_path, below, media)) == 'graphic'
    assert classify_docx(_write(tmp_path, at, media)) == 'text'


def test_stats_count_everything(tmp_path: Path):
    stats = docx_stats(_write(tmp_path, 'abc def', {'big.png': MIN_IMAGE_BYTES, 'icon.gif': 10}))

    assert stats == (6, 2, 1)


def test_broken_zip_raises(tmp_path: Path):
    path = tmp_path / 'broken.docx'
    path.write_bytes(b'PK\x03\x04broken')

    with pytest.raises(zipfile.BadZipFile):
        classify_docx(path)
