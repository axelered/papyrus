"""Builders for small but structurally real documents, used to pin magika labels"""

import io
import os
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


# draws the image `Im1` over a whole `pdf_streams` page, which is 200 x 200 points
PDF_IMAGE = b'q 200 0 0 200 0 0 cm /Im1 Do Q '


def pdf_text(*lines: bytes, invisible: bool = False) -> bytes:
    """Helvetica text operators, one line every 10 points from the top; `invisible` is render mode 3, the OCR layer a
    scanner puts behind its image. Text beyond the page edge is not extracted, keep lines under 45 characters"""
    shown = b' '.join(b'(%s) Tj 0 -10 Td' % line for line in lines)
    return b'BT %s/F1 8 Tf 5 185 Td %s ET ' % (b'3 Tr ' if invisible else b'', shown)


def pdf_streams(*streams: bytes) -> bytes:
    """PDF with one 200 x 200 page per content stream, a Helvetica font `F1` and a 1 x 1 gray image `Im1`"""
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 '
        b'/Length 1 >>\nstream\n\x80\nendstream',
    ]
    kids = []
    for stream in streams:
        number = len(objects) + 1
        kids.append(b'%d 0 R' % number)
        objects.append(
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents %d 0 R '
            b'/Resources << /Font << /F1 3 0 R >> /XObject << /Im1 4 0 R >> >> >>' % (number + 1)
        )
        objects.append(b'<< /Length %d >>\nstream\n%s\nendstream' % (len(stream), stream))
    objects[1] = b'<< /Type /Pages /Kids [%s] /Count %d >>' % (b' '.join(kids), len(kids))
    body, offsets = b'%PDF-1.4\n', []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(body))
        body += b'%d 0 obj\n%s\nendobj\n' % (number, content)
    xref = len(body)
    body += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    body += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    body += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref)
    return body


def docx_media(text: str = '', media: dict[str, int] | None = None) -> bytes:
    """DOCX with `text` in one run and `word/media/` entries of the given sizes, in random bytes"""
    paragraph = f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
    document = _DOCUMENT.split('<w:body>')[0] + f'<w:body>{paragraph}</w:body></w:document>'
    entries: dict[str, str | bytes] = {
        '[Content_Types].xml': _CONTENT_TYPES,
        '_rels/.rels': _RELS,
        'word/document.xml': document,
    }
    for name, size in (media or {}).items():
        entries[f'word/media/{name}'] = os.urandom(size)
    return _zip(entries)


_ODT_MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
    '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
    '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
    '</manifest:manifest>'
)
_ODT_CONTENT = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2">'
    '<office:body><office:text>{body}</office:text></office:body></office:document-content>'
)
ODT_REPORT = '<text:h text:outline-level="1">Report</text:h><text:p>Revenue grew.</text:p>'


def odt(body: str = ODT_REPORT) -> bytes:
    """`mimetype` must be the first entry and stored uncompressed"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mimetype', 'application/vnd.oasis.opendocument.text', zipfile.ZIP_STORED)
        archive.writestr('META-INF/manifest.xml', _ODT_MANIFEST, zipfile.ZIP_DEFLATED)
        archive.writestr('content.xml', _ODT_CONTENT.format(body=body), zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def odt_linked_image(url: str) -> str:
    return (
        '<text:p><draw:frame svg:width="1cm" svg:height="1cm" text:anchor-type="as-char">'
        f'<draw:image xlink:href="{url}" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/>'
        '</draw:frame></text:p>'
    )


def odt_linked_section(url: str) -> str:
    return (
        '<text:section text:name="linked">'
        f'<text:section-source xlink:href="{url}" xlink:type="simple" xlink:show="embed"/>'
        '<text:p>placeholder</text:p></text:section>'
    )


RTF = rb'{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}{\pard\s1\b\fs32 Report\par}{\pard Revenue grew.\par}}'


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


def image(format: str, size: tuple[int, int] = (64, 48), mode: str = 'RGB', frames: int = 1, **options) -> bytes:
    """Solid image; frames get distinct colors so frame selection is observable"""
    from PIL import Image

    colors = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)]
    images = [Image.new('RGB', size, colors[index % len(colors)]).convert(mode) for index in range(frames)]
    buffer = io.BytesIO()
    if frames > 1:
        options.update(save_all=True, append_images=images[1:])
    images[0].save(buffer, format=format, **options)
    return buffer.getvalue()


def docx_pages(count: int) -> bytes:
    """Word document with `Page <n>` on each of `count` pages, separated by hard page breaks"""
    breaks = '<w:r><w:br w:type="page"/></w:r>'
    body = ''.join(
        f'<w:p><w:r><w:t>Page {number}</w:t></w:r>{breaks if number < count else ""}</w:p>'
        for number in range(1, count + 1)
    )
    return _zip(
        {
            '[Content_Types].xml': _CONTENT_TYPES,
            '_rels/.rels': _RELS,
            'word/document.xml': (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{body}</w:body></w:document>'
            ),
        }
    )


def pptx_slides(count: int) -> bytes:
    from pptx import Presentation

    presentation = Presentation()
    for number in range(1, count + 1):
        presentation.slides.add_slide(presentation.slide_layouts[1]).shapes.title.text = f'Slide {number}'
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


# long enough for several A4 pages once LibreOffice paginates it
LONG_HTML = (
    '<html><head><title>Long</title></head><body>'
    + ''.join(f'<h2>Section {number}</h2><p>{"lorem ipsum dolor sit amet " * 40}</p>' for number in range(1, 30))
    + '</body></html>'
).encode()


def pdf_pages(*sizes: tuple[float, float], rotate: int = 0) -> bytes:
    """Blank PDF with one page per (width, height) in points, optionally rotated by /Rotate"""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument.new()
    for width, height in sizes:
        page = document.new_page(width, height)
        if rotate:
            page.set_rotation(rotate)
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


def png_header(width: int, height: int) -> bytes:
    """PNG that declares a huge size with no pixel data: trips the bomb guard at open, without decoding"""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IEND', b'')


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
    'png': lambda: image('PNG'),
    'jpeg': lambda: image('JPEG'),
    'webp': lambda: image('WEBP'),
    'gif': lambda: image('GIF'),
    'bmp': lambda: image('BMP'),
    'tiff': lambda: image('TIFF'),
    'avif': lambda: image('AVIF'),
}
