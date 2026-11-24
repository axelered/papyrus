import base64
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# mirrors server/types without bounds and validators, test/sdk/test_schema.py fails when they drift (adr/sdk-python.md)


class ImageFormat(StrEnum):
    PNG = 'png'
    JPEG = 'jpeg'
    WEBP = 'webp'


class Fit(StrEnum):
    """How a page fills the thumbnail box"""

    CONTAIN = 'contain'
    COVER = 'cover'


class WaitUntil(StrEnum):
    """Page event awaited before a screenshot"""

    LOAD = 'load'
    DOMCONTENTLOADED = 'domcontentloaded'
    NETWORKIDLE = 'networkidle'


class Tokenizer(StrEnum):
    O200K_BASE = 'o200k_base'
    CL100K_BASE = 'cl100k_base'
    WORD = 'word'
    CHARACTER = 'character'


class Encoding(StrEnum):
    """How `result.content` carries the artifact"""

    UTF8 = 'utf-8'
    BASE64 = 'base64'


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


class ChunkKind(StrEnum):
    TEXT = 'text'
    TABLE = 'table'
    CODE = 'code'


class CodeLanguage(StrEnum):
    PYTHON = 'python'
    JAVASCRIPT = 'javascript'
    TYPESCRIPT = 'typescript'
    TSX = 'tsx'
    JAVA = 'java'
    KOTLIN = 'kotlin'
    GO = 'go'
    RUST = 'rust'
    C = 'c'
    CPP = 'cpp'
    CSHARP = 'csharp'
    PHP = 'php'
    RUBY = 'ruby'
    SWIFT = 'swift'
    SCALA = 'scala'
    BASH = 'bash'
    POWERSHELL = 'powershell'
    SQL = 'sql'
    YAML = 'yaml'
    TOML = 'toml'
    JSON = 'json'
    HTML = 'html'
    CSS = 'css'
    DOCKERFILE = 'dockerfile'
    HCL = 'hcl'


class ApiModel(BaseModel):
    """Wire model: camelCase JSON keys, Python names in code"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Fetch(ApiModel):
    url: str
    """Final URL after redirects"""
    status: int | None = None
    """HTTP status of the document"""


class FileInfo(ApiModel):
    """File facts shared by `source` and `result`; unknown facts are None"""

    uri: str | None = None
    """Source URI, or the destination the result was written to"""
    name: str | None = None
    size: int | None = None
    """Bytes"""
    mime_type: str | None = None
    extension: str | None = None
    """Without the dot"""
    label: str | None = None
    """Papyrus format label or magika content type label; the language for code"""
    group: FormatGroup | None = None
    page_count: int | None = None
    """Pages or frames, when the operation knows it"""
    width: int | None = None
    """Pixels, raster images only"""
    height: int | None = None
    """Pixels, raster images only"""
    title: str | None = None
    """Document title, when extracted"""


class Source(FileInfo):
    """The input document; navigated pages have no file facts"""

    uri: str
    fetch: Fetch | None = None
    """Present for http(s) sources"""


class Result(FileInfo):
    """The primary artifact; without content when it was written to a destination"""

    name: str
    size: int
    mime_type: str
    extension: str
    label: str
    group: FormatGroup
    encoding: Encoding | None = None
    content: str | None = Field(default=None, repr=False)
    """As sent by the server, utf-8 text or base64: prefer `data` and `text`"""

    @property
    def data(self) -> bytes:
        """The artifact as bytes"""
        content = self._content()
        return base64.b64decode(content) if self.encoding == Encoding.BASE64 else content.encode()

    @property
    def text(self) -> str:
        """The artifact as text, ValueError when it is binary"""
        content = self._content()
        if self.encoding != Encoding.UTF8:
            raise ValueError(f'{self.name} is binary ({self.mime_type}), use data or save()')
        return content

    def save(self, path: str | Path) -> Path:
        """Writes the artifact to a local file, replacing it"""
        target = Path(path)
        target.write_bytes(self.data)
        return target

    def _content(self) -> str:
        if self.content is None:
            raise ValueError(f'the result was written to {self.uri}, the response has no content')
        return self.content


class ChunkingOptions(ApiModel):
    """Only the fields set here are sent, the server applies its own defaults"""

    max_tokens: int = 512
    """Token budget of every chunk"""
    overlap: int = 0
    """Tokens of the next prose chunk appended to each prose chunk"""
    tokenizer: Tokenizer = Tokenizer.O200K_BASE


class MarkdownOptions(ApiModel):
    chunking: ChunkingOptions | None = None


class ThumbnailOptions(ApiModel):
    page: int = 1
    width: int = 256
    height: int = 256
    fit: Fit = Fit.CONTAIN
    format: ImageFormat = ImageFormat.WEBP


class RenderOptions(ApiModel):
    page: int = 1
    width: int | None = None
    height: int | None = None
    dpi: int | None = None
    format: ImageFormat = ImageFormat.PNG


class ScreenshotOptions(ApiModel):
    width: int = 1280
    height: int = 800
    full_page: bool = False
    scale: int = 1
    wait_until: WaitUntil = WaitUntil.LOAD
    delay: int = 0
    format: ImageFormat = ImageFormat.PNG


class InspectOptions(ApiModel):
    pass


class InspectChecks(ApiModel):
    """Every check is True when passed, False when failed and None when it does not apply"""

    extension_match: bool | None = None


class Chunk(ApiModel):
    index: int
    kind: ChunkKind
    text: str
    """Markdown of the chunk, with the overlap appended for prose"""
    language: CodeLanguage | None = None
    """Code chunks only, None if not curated"""
    headings: list[str]
    """Headings the chunk sits under, outermost first"""
    slide: int | None = None
    """pptx only"""
    start: int
    """Offset of the chunk span in `result.text` (overlap not included)"""
    end: int
    """End offset of the chunk span in `result.text`, exclusive"""
    tokens: int


class OpModel(ApiModel):
    source: Source
    request_id: str | None = Field(default=None, exclude=True)
    """`X-Request-ID` of the response, to find the request in the server logs"""


class OpResponse(OpModel):
    result: Result


class MarkdownResponse(OpResponse):
    options: MarkdownOptions
    chunks: list[Chunk] | None = None
    """Present with `chunking`, None with a destination"""


class ThumbnailResponse(OpResponse):
    options: ThumbnailOptions


class RenderResponse(OpResponse):
    options: RenderOptions


class ScreenshotResponse(OpResponse):
    options: ScreenshotOptions


class InspectResponse(OpModel):
    options: InspectOptions
    checks: InspectChecks


class StatusResponse(ApiModel):
    status: Literal['ok'] = 'ok'


class ProblemIssue(ApiModel):
    """One invalid field of a `validation_error`"""

    loc: list[str | int]
    msg: str
    type: str


class Problem(ApiModel):
    """RFC 9457 error body"""

    type: str = 'about:blank'
    title: str
    status: int
    detail: str | None = None
    code: str
    errors: list[ProblemIssue] | None = None
