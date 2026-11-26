from pathlib import Path

import pypdfium2 as pdfium
import pytest

from server.ocr.classify.pdf import classify_pdf, classify_pdf_page
from test.fixtures.samples import PDF_IMAGE, pdf_streams, pdf_text

# 42 characters, 36 of them not whitespace: above MIN_CHARS, below SCAN_CHARS
LINE = b'Invisible OCR layer of a scanned page 1234'
PROSE = pdf_text(*[LINE] * 6)


def _write(tmp_path: Path, *streams: bytes) -> Path:
    path = tmp_path / 'sample.pdf'
    path.write_bytes(pdf_streams(*streams))
    return path


@pytest.mark.parametrize(
    ('stream', 'expected'),
    [
        (b'', 'empty'),
        (b'10 10 m 20 10 l S', 'empty'),  # a lone rule
        (b'10 10 150 150 re f', 'graphic'),  # text drawn as outlines
        (PDF_IMAGE, 'graphic'),  # a scan without text layer
        (pdf_text(bytes(range(1, 31)), bytes(range(1, 31))), 'garbage'),  # glyphs mapped to control characters
        (PDF_IMAGE + pdf_text(LINE, invisible=True), 'scan'),  # a thin OCR layer behind the image
        (PDF_IMAGE + pdf_text(LINE), 'text'),  # a photo cover with a visible title
        (PDF_IMAGE + pdf_text(*[LINE] * 6, invisible=True), 'text'),  # a scan with a usable OCR layer
        (PROSE, 'text'),
    ],
)
def test_classifies_pages(tmp_path: Path, stream: bytes, expected: str):
    pdf = pdfium.PdfDocument(_write(tmp_path, stream))
    try:
        assert classify_pdf_page(pdf[0]) == expected
    finally:
        pdf.close()


def test_page_sized_paths_are_backgrounds(tmp_path: Path):
    path = _write(tmp_path, b'0 0 200 200 re f')
    pdf = pdfium.PdfDocument(path)
    try:
        assert classify_pdf_page(pdf[0]) == 'empty'
    finally:
        pdf.close()


@pytest.mark.parametrize(
    ('streams', 'expected'),
    [
        ((PROSE, b'', PROSE), 'text'),  # separator pages do not trigger OCR
        ((PROSE, PDF_IMAGE, PROSE), 'graphic'),  # any OCR page settles the document
        ((b'', b''), 'empty'),
    ],
)
def test_classifies_documents(tmp_path: Path, streams: tuple[bytes, ...], expected: str):
    assert classify_pdf(_write(tmp_path, *streams)) == expected


def test_unreadable_pdf_raises(tmp_path: Path):
    path = tmp_path / 'broken.pdf'
    path.write_bytes(b'%PDF-1.4\nbroken')

    with pytest.raises(pdfium.PdfiumError):
        classify_pdf(path)
