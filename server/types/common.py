from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OutputMode(StrEnum):
    FILE = 'file'
    JSON = 'json'


class FormatGroup(StrEnum):
    DOCUMENT = 'document'
    SPREADSHEET = 'spreadsheet'
    PRESENTATION = 'presentation'
    EMAIL = 'email'
    ARCHIVE = 'archive'
    MARKUP = 'markup'
    DATA = 'data'
    CODE = 'code'
    TEXT = 'text'
    UNKNOWN = 'unknown'


class FormatLabel(StrEnum):
    """Explicit formats; values are magika labels except rss/atom, which magika reports as xml"""

    PDF = 'pdf'
    DOCX = 'docx'
    PPTX = 'pptx'
    XLSX = 'xlsx'
    EPUB = 'epub'
    ZIP = 'zip'
    DOC = 'doc'
    PPT = 'ppt'
    XLS = 'xls'
    OUTLOOK = 'outlook'
    RTF = 'rtf'
    ODT = 'odt'
    ODS = 'ods'
    ODP = 'odp'
    TXT = 'txt'
    MARKDOWN = 'markdown'
    CSV = 'csv'
    TSV = 'tsv'
    HTML = 'html'
    XML = 'xml'
    RSS = 'rss'
    ATOM = 'atom'
    JSON = 'json'
    JSONL = 'jsonl'
    IPYNB = 'ipynb'
    UNKNOWN = 'unknown'


class OpRequest(BaseModel):
    """Common request contract; operations subclass it, override `output` defaults and add typed `options`"""

    model_config = ConfigDict(extra='forbid')

    source: str = Field(description='file://<part>, http(s):// URL or <protocol>://<connector>/<path>')
    destination: str | None = Field(default=None, description='Connector URI to write the result to')
    output: OutputMode = OutputMode.JSON


class SourceType(BaseModel):
    mime_type: str = Field(description='Explicit format MIME or the MIME magika reports for derived formats')
    extension: str
    label: str = Field(description='Papyrus format label or magika content type label; the language for code')
    group: FormatGroup


class Receipt(BaseModel):
    destination: str
    content_type: str
    size: int
    source_type: SourceType | None = None


class RootResponse(BaseModel):
    status: str = 'ok'
    name: str = 'Axelered Papyrus'


class StatusResponse(BaseModel):
    status: str = 'ok'
