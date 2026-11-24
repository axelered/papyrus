"""
Heuristic to classify DOCX documents in different content categories. This utility powers the auto OCR decision.

Unlike PDF, DOCX text is the document XML itself: it is always readable, and the only text markitdown misses is
inside images. OCR pays off only when the images carry the content: a scan wrapped in Word.

Classes:
- `empty`: almost no text and no raster images.
- `graphic`: almost no text next to raster images of any size, or under `CHARS_PER_IMAGE` characters per content
  image (scans pasted in Word, a captioned map).
- `text`: a usable text layer, including native documents with some screenshots (their image text is not recovered).

The decision is per document: a photo cover or section background cannot send a report to OCR, but a document mixing
native pages with scanned pages is classed `text` too, its scanned pages are not recovered. Telling pages apart would
need page boundaries, which DOCX does not reliably record.

Functions:
- `classify_docx` classifies the whole document
- `docx_stats` measures what the classification is based on

Legacy formats (`doc`, `rtf`, `odt`) are classified after their LibreOffice conversion to `docx`. Images are judged by
their declared zip entry size, nothing is decompressed but `word/document.xml`, which is streamed and only read until
the class is settled.
"""

import zipfile
from os import PathLike
from typing import Literal, NamedTuple
from xml.etree import ElementTree

type DocxClass = Literal['empty', 'graphic', 'text']

MIN_CHARS = 32  # below this a document has no usable text
MIN_IMAGE_BYTES = 32 * 1024  # smaller images are logos, icons and decorations once the document has text
CHARS_PER_IMAGE = 200  # text per content image below which the images carry the content

DOCUMENT = 'word/document.xml'
MEDIA = 'word/media/'
RASTER = ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.gif', '.webp')  # emf and wmf are vector drawings
TEXT_TAG = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'


class DocxStats(NamedTuple):
    chars: int  # alphanumeric characters of the body text, text boxes included
    rasters: int  # raster images of any size
    images: int  # content images: rasters of at least MIN_IMAGE_BYTES


def classify_docx(path: str | PathLike[str]) -> DocxClass:
    with zipfile.ZipFile(path) as docx:
        rasters, images = _count_images(docx)
        chars = _count_chars(docx, limit=max(MIN_CHARS, CHARS_PER_IMAGE * images))
    return _classify(DocxStats(chars, rasters, images))


def docx_stats(path: str | PathLike[str]) -> DocxStats:
    with zipfile.ZipFile(path) as docx:
        rasters, images = _count_images(docx)
        return DocxStats(_count_chars(docx), rasters, images)


def _classify(stats: DocxStats) -> DocxClass:
    if stats.chars < MIN_CHARS:
        return 'graphic' if stats.rasters else 'empty'
    if stats.images and stats.chars < CHARS_PER_IMAGE * stats.images:
        return 'graphic'
    return 'text'


def _count_images(docx: zipfile.ZipFile) -> tuple[int, int]:
    """Raster images of any size, and content images of at least MIN_IMAGE_BYTES"""
    sizes = [e.file_size for e in docx.infolist() if _is_raster(e.filename.lower())]
    return len(sizes), sum(size >= MIN_IMAGE_BYTES for size in sizes)


def _is_raster(name: str) -> bool:
    return name.startswith(MEDIA) and name.endswith(RASTER)


def _count_chars(docx: zipfile.ZipFile, limit: int | None = None) -> int:
    """Alphanumeric characters of every `w:t` run, streamed and stopped at `limit` since only the threshold matters"""
    chars = 0
    with docx.open(DOCUMENT) as document:
        for _, element in ElementTree.iterparse(document):
            if element.tag == TEXT_TAG and element.text:
                chars += sum(c.isalnum() for c in element.text)
                if limit is not None and chars >= limit:
                    break
            element.clear()
    return chars
