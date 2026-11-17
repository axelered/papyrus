from enum import StrEnum


class ConvertFormat(StrEnum):
    """Conversion targets; values are the LibreOfficeKit saveAs format names"""

    DOCX = 'docx'
    XLSX = 'xlsx'
    PPTX = 'pptx'
    PDF = 'pdf'
