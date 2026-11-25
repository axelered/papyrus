from pathlib import Path

import pytest

from server.ocr.classify import UNREADABLE, needs_ocr
from test.fixtures.samples import PDF_IMAGE, docx_media, pdf_streams


@pytest.mark.parametrize(
    ('name', 'content', 'label', 'expected'),
    [
        ('scan.pdf', pdf_streams(PDF_IMAGE), 'pdf', (True, 'graphic')),
        ('blank.pdf', pdf_streams(b''), 'pdf', (False, 'empty')),
        ('scan.docx', docx_media('', {'scan.png': 40_000}), 'docx', (True, 'graphic')),
        ('broken.pdf', b'%PDF-1.4\nbroken', 'pdf', (False, UNREADABLE)),
        ('broken.docx', b'PK\x03\x04broken', 'docx', (False, UNREADABLE)),
        ('data.csv', b'a,b\n1,2\n', 'csv', (False, 'text')),
    ],
)
def test_decides_by_format(tmp_path: Path, name: str, content: bytes, label: str, expected: tuple[bool, str]):
    path = tmp_path / name
    path.write_bytes(content)

    assert needs_ocr(path, label) == expected
