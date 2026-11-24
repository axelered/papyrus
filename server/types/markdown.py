from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from server.types.common import OPTIONS, OUTPUT, RESOLVED, ApiModel, OpRequest, OpResponse, OptionsModel, OutputMode


class ReadMode(StrEnum):
    TEXT = 'text'
    OCR = 'ocr'


class Tokenizer(StrEnum):
    O200K_BASE = 'o200k_base'
    CL100K_BASE = 'cl100k_base'
    WORD = 'word'
    CHARACTER = 'character'

    @property
    def is_tiktoken(self) -> bool:
        return self in {Tokenizer.O200K_BASE, Tokenizer.CL100K_BASE}


class ChunkKind(StrEnum):
    TEXT = 'text'
    TABLE = 'table'
    CODE = 'code'


class CodeLanguage(StrEnum):
    """Curated tree-sitter grammars baked into the image; values are tree-sitter language names"""

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


class ChunkingOptions(OptionsModel):
    max_tokens: int = Field(default=512, ge=64, le=8192, description='Token budget of every chunk')
    overlap: int = Field(default=0, ge=0, description='Tokens of the next prose chunk appended to each prose chunk')
    tokenizer: Tokenizer = Field(
        default=Tokenizer.O200K_BASE,
        description='Token counting: `o200k_base` (GPT-4o and later), `cl100k_base` (GPT-4), `word` or `character`',
    )

    @model_validator(mode='after')
    def _overlap_below_budget(self) -> Self:
        if self.overlap >= self.max_tokens:
            raise ValueError('overlap must be smaller than max_tokens')
        return self


class MarkdownOptions(OptionsModel):
    read_mode: ReadMode = Field(
        default=ReadMode.TEXT,
        description=(
            '`text` extracts the text stored in the file, `ocr` reads page images with a vision model. Formats without '
            'pages fall back to `text`; the response reports the mode used'
        ),
    )
    chunking: ChunkingOptions | None = Field(
        default=None, description='Structure-aware chunks; the file and destination output become the JSON document'
    )


class MarkdownRequest(OpRequest):
    output: OutputMode = Field(default=OutputMode.JSON, description=OUTPUT)
    options: MarkdownOptions = Field(default_factory=MarkdownOptions, description=OPTIONS)


class Chunk(ApiModel):
    index: int = Field(description='Position in the document, from 0')
    kind: ChunkKind = Field(description='`text` prose, `table` rows with the header repeated, `code` a fenced block')
    text: str = Field(description='Markdown of the chunk, with the overlap appended for prose')
    language: CodeLanguage | None = Field(default=None, description='Code chunks only, omitted if not curated')
    headings: list[str] = Field(description='Headings the chunk sits under, outermost first')
    slide: int | None = Field(default=None, description='pptx, and every presentation read with `ocr`')
    page: int | None = Field(default=None, description='Page the chunk starts on, read with `ocr`, not presentations')
    start: int = Field(description='Offset of the chunk span in `result.content` (overlap not included)')
    end: int = Field(description='End offset of the chunk span in `result.content`, exclusive')
    tokens: int = Field(description='Tokens in `text`, counted with the requested tokenizer')


class MarkdownResponse(OpResponse):
    options: MarkdownOptions = Field(description=RESOLVED)
    chunks: list[Chunk] | None = Field(
        default=None, description='Present when options.chunking is set, omitted with a destination'
    )
