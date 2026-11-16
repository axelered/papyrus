"""Builders for small but structurally real documents, used to pin magika labels"""

import io
import zipfile
from collections.abc import Callable

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>'
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)
_DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Report</w:t></w:r></w:p>'
    '<w:p><w:r><w:t>Revenue grew.</w:t></w:r></w:p></w:body></w:document>'
)


def _zip(entries: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def docx() -> bytes:
    return _zip({'[Content_Types].xml': _CONTENT_TYPES, '_rels/.rels': _RELS, 'word/document.xml': _DOCUMENT})


def xlsx() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Revenue'
    sheet.append(['region', 'q1', 'q2'])
    sheet.append(['eu', 10, 11])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def pptx() -> bytes:
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = 'Roadmap'
    slide.placeholders[1].text = 'Ship chunking'
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def pdf() -> bytes:
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> >> >>',
        None,
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    stream = b'BT /F1 12 Tf 20 100 Td (Hello PDF) Tj ET'
    objects[3] = b'<< /Length %d >>\nstream\n%s\nendstream' % (len(stream), stream)
    body, offsets = b'%PDF-1.4\n', []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(body))
        body += b'%d 0 obj\n%s\nendobj\n' % (number, content)
    xref = len(body)
    body += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    body += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    body += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref)
    return body


def zip_archive() -> bytes:
    return _zip({'notes.txt': 'first file\n', 'data.csv': 'a,b\n1,2\n'})


HTML = (
    b'<!DOCTYPE html><html><head><title>Quarterly</title></head><body><h1>Report</h1>'
    b'<p>Revenue grew in every region this quarter.</p><ul><li>EU</li><li>US</li></ul></body></html>'
)
CSV = b'name,amount,region\nalpha,1,eu\nbeta,2,us\ngamma,3,apac\ndelta,4,latam\n'
JSON = (
    b'{\n  "name": "papyrus",\n  "version": 1,\n  "tags": [\n    "documents",\n    "markdown"\n  ],\n'
    b'  "nested": {\n    "enabled": true\n  }\n}\n'
)
JSONL = b'{"id": 1, "text": "first"}\n{"id": 2, "text": "second"}\n{"id": 3, "text": "third"}\n'
MARKDOWN = b'# Title\n\nSome **bold** text and a [link](https://example.com).\n\n## Section\n\n- one\n- two\n'
PYTHON = (
    b'import os\n\n\ndef load(path: str) -> bytes:\n    """Read a file."""\n'
    b'    with open(path, "rb") as f:\n        return f.read()\n\n\n'
    b'class Store:\n    def __init__(self, root: str) -> None:\n        self.root = root\n'
)
TYPESCRIPT = (
    b'export interface User {\n  id: number;\n  name: string;\n}\n\n'
    b'export function greet(user: User): string {\n  return `Hello ${user.name}`;\n}\n'
)

SAMPLES: dict[str, Callable[[], bytes]] = {
    'pdf': pdf,
    'docx': docx,
    'xlsx': xlsx,
    'pptx': pptx,
    'zip': zip_archive,
    'html': lambda: HTML,
    'csv': lambda: CSV,
    'json': lambda: JSON,
    'jsonl': lambda: JSONL,
    'markdown': lambda: MARKDOWN,
    'python': lambda: PYTHON,
    'typescript': lambda: TYPESCRIPT,
}
