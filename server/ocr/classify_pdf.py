"""
Heuristic to classify PDF documents in different content categories. This utility power the auto OCR decision.

Classes:
- `empty`: almost no text and almost nothing drawn (blank pages, a lone line or page number).
- `graphic`: visible content but almost no text (scans, text drawn as outlines).
- `garbage`: a text layer with too many glyphs pdfium could not map to Unicode (fonts without ToUnicode).
- `scan`: a page-sized image with a little text on top (a stamped header or page number).
- `text`: a usable text layer, including scans that already carry one.

Functions
- `classify_pdf_page` classify a single page
- `classify_pdf`

"""

import unicodedata
from os import PathLike
from typing import Literal

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

type PageClass = Literal['empty', 'graphic', 'garbage', 'scan', 'text']

MIN_CHARS = 32  # below this a page has no usable text
CONTENT_COVERAGE = 0.1  # drawn share that makes a textless page non-empty
GARBAGE_RATIO = 0.1  # unmapped glyph share that makes a text layer unusable
SCAN_CHARS = 200  # text a scan can carry (stamp, header, page number)
SCAN_COVERAGE = 0.8  # image share of the page that makes it a scan
BACKGROUND = 0.9  # paths larger than this share of the page are backgrounds


def classify_pdf(path: str | PathLike[str]) -> PageClass:
    pdf = pdfium.PdfDocument(path)
    result: PageClass = 'empty'
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                page_class = classify_pdf_page(page)
            finally:
                page.close()
            if page_class in {'graphic', 'garbage', 'scan'}:
                return page_class
            if page_class == 'text':
                result = 'text'
    finally:
        pdf.close()
    return result


def classify_pdf_page(page: pdfium.PdfPage) -> PageClass:
    text = _text(page)
    image_area, content_area = _coverage(page)
    if len(text) < MIN_CHARS:
        return 'graphic' if content_area > CONTENT_COVERAGE else 'empty'
    if _garbage_ratio(text) > GARBAGE_RATIO:
        return 'garbage'
    if len(text) < SCAN_CHARS and image_area > SCAN_COVERAGE:
        return 'scan'
    return 'text'


def _text(page: pdfium.PdfPage) -> list[str]:
    """Non-whitespace characters of the page text layer"""
    textpage = page.get_textpage()
    try:
        return [c for c in textpage.get_text_bounded() if not c.isspace()]
    finally:
        textpage.close()


def _coverage(page: pdfium.PdfPage) -> tuple[float, float]:
    """Shares of the page covered by images, and by images and paths: bounding boxes summed and capped at 1

    Page-sized paths are backgrounds and excluded, page-sized images are kept: that is what a scan looks like.
    Overlapping and off-page boxes count in full, which the cap absorbs for these checks.
    """
    # todo: bounds of objects nested in form XObjects may be in form space, check against real samples
    width, height = page.get_size()
    area = width * height
    if area <= 0:
        return 0.0, 0.0
    images = paths = 0.0
    for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE, pdfium_c.FPDF_PAGEOBJ_PATH)):
        left, bottom, right, top = obj.get_bounds()
        box = max(0.0, right - left) * max(0.0, top - bottom)
        if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
            images += box
        elif box < BACKGROUND * area:
            paths += box
    return min(1.0, images / area), min(1.0, (images + paths) / area)


def _garbage_ratio(text: list[str]) -> float:
    """Share of U+FFFD, private-use and control characters: glyphs pdfium could not map to Unicode"""
    if not text:
        return 0.0
    return sum(c == '�' or unicodedata.category(c) in ('Co', 'Cc') for c in text) / len(text)
