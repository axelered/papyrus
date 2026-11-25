"""
Decides whether a document needs OCR: picks the classifier of its format and maps the class to the decision.

Classes needing OCR are `graphic`, `garbage` and `scan` (`classify_pdf.OCR_CLASSES`, DOCX only has `graphic`); a file
the classifier cannot open does not need OCR, the text reader then reports it.
"""

import zipfile
from os import PathLike
from xml.etree import ElementTree

import pypdfium2 as pdfium

from server.ocr.classify_docx import classify_docx
from server.ocr.classify_pdf import OCR_CLASSES, classify_pdf

UNREADABLE = 'unreadable'
# not a zip, no document.xml, broken XML
_DOCX_ERRORS = (zipfile.BadZipFile, KeyError, ElementTree.ParseError)


def needs_ocr(path: str | PathLike[str], label: str) -> tuple[bool, str]:
    """Decision and class; formats without a classifier never need OCR"""
    content = _classify(path, label)
    return content in OCR_CLASSES, content


def _classify(path: str | PathLike[str], label: str) -> str:
    try:
        if label == 'pdf':
            return classify_pdf(path)
        if label == 'docx':
            return classify_docx(path)
    except (pdfium.PdfiumError, *_DOCX_ERRORS):
        return UNREADABLE
    return 'text'
