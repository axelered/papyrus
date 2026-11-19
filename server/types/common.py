from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class OutputMode(StrEnum):
    FILE = 'file'
    JSON = 'json'


# longest side of any produced raster, bounds worker and browser memory
MAX_IMAGE_SIDE = 4096


class ImageFormat(StrEnum):
    """Raster outputs shared by image producing operations"""

    PNG = 'png'
    JPEG = 'jpeg'
    WEBP = 'webp'

    @property
    def mime_type(self) -> str:
        return f'image/{self.value}'

    @property
    def extension(self) -> str:
        return 'jpg' if self is ImageFormat.JPEG else self.value


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
    IMAGE = 'image'
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
    PNG = 'png'
    JPEG = 'jpeg'
    WEBP = 'webp'
    GIF = 'gif'
    BMP = 'bmp'
    TIFF = 'tiff'
    AVIF = 'avif'
    UNKNOWN = 'unknown'


class ApiModel(BaseModel):
    """Wire model: camelCase JSON keys, Python names in code

    Python names stay valid for constructors; the API boundary validates with `by_name=False` (adr/api.md).
    """

    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_alias=True, validate_by_name=True, serialize_by_alias=True
    )


OUTPUT = (
    '`json` returns the envelope with the artifact inline, `file` returns the artifact as the response body; '
    'with a destination the response is always the light JSON envelope'
)
OPTIONS = 'Operation options, omitted fields take their defaults'
RESOLVED = 'The request options with defaults applied'
FORMAT = 'Output image format'


class OpRequest(ApiModel):
    """Common request contract; operations subclass it, override `output` defaults and add typed `options`"""

    model_config = ConfigDict(extra='forbid')

    source: str = Field(description='file://<part>, http(s):// URL or <protocol>://<connector>/<path>')
    destination: str | None = Field(default=None, description='Connector URI to write the result to')
    output: OutputMode = Field(default=OutputMode.JSON, description=OUTPUT)


class OptionsModel(ApiModel):
    """Base of every op `options` model; echoed back resolved in the response"""

    model_config = ConfigDict(extra='forbid')


class Encoding(StrEnum):
    """How `result.content` carries the artifact"""

    UTF8 = 'utf-8'
    BASE64 = 'base64'


_MIME = 'Explicit format MIME or the MIME magika reports for derived formats'
_LABEL = 'Papyrus format label or magika content type label; the language for code'
_NAME = 'File name'
_EXTENSION = 'File extension of the format, without the dot'
_GROUP = 'Format family, e.g. `document` or `image`'


class FileInfo(ApiModel):
    """File facts shared by `source` and `result`; unknown fields are omitted"""

    uri: str | None = Field(default=None, description='Source URI, or the destination the result was written to')
    name: str | None = Field(default=None, description=_NAME)
    size: int | None = Field(default=None, description='Bytes')
    mime_type: str | None = Field(default=None, description=_MIME)
    extension: str | None = Field(default=None, description=_EXTENSION)
    label: str | None = Field(default=None, description=_LABEL)
    group: FormatGroup | None = Field(default=None, description=_GROUP)
    page_count: int | None = Field(default=None, description='Pages or frames, when the operation knows it')
    width: int | None = Field(default=None, description='Pixels, raster images only')
    height: int | None = Field(default=None, description='Pixels, raster images only')
    title: str | None = Field(default=None, description='Document title, when extracted')


class Fetch(ApiModel):
    url: str = Field(description='Final URL after redirects')
    status: int | None = Field(default=None, description='HTTP status of the document, omitted when none was sent')


class Source(FileInfo):
    """The input document; file fields are omitted for navigated pages, which have no file"""

    uri: str = Field(description='The request `source`')
    fetch: Fetch | None = Field(default=None, description='Present for http(s) sources')


class Result(FileInfo):
    """The primary artifact; `content` is inline unless a destination is set"""

    name: str = Field(description=_NAME)
    size: int = Field(description='Bytes')
    mime_type: str = Field(description=_MIME)
    extension: str = Field(description=_EXTENSION)
    label: str = Field(description=_LABEL)
    group: FormatGroup = Field(description=_GROUP)
    encoding: Encoding | None = Field(default=None, description='Encoding of `content`, omitted with a destination')
    content: str | None = Field(
        default=None, description='The artifact as utf-8 text or base64, omitted with a destination'
    )


class OpResponse(ApiModel):
    """Envelope of every operation; subclasses type `options` and add data sections defaulting to None"""

    source: Source = Field(description='The input document')
    result: Result = Field(description='The primary artifact')


class HelloResponse(ApiModel):
    status: Literal['ok'] = Field(default='ok', description='Always `ok`')
    name: str = Field(default='Axelered Papyrus', description='Product name')
    version: str = Field(description='Release version, `0.0.0` in local development')
    watermark: str | None = Field(
        default=None, description='Build stamp with version, commit and date; omitted locally'
    )


class StatusResponse(ApiModel):
    status: Literal['ok'] = Field(default='ok', description='Always `ok`')
