from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.types.common import OpRequest, OutputMode, SourceType


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


class ChunkingOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')

    max_tokens: int = Field(default=512, ge=64, le=8192, description='Token budget of every chunk')
    overlap: int = Field(default=0, ge=0, description='Tokens of the next prose chunk appended to each prose chunk')
    tokenizer: Tokenizer = Tokenizer.O200K_BASE

    @model_validator(mode='after')
    def _overlap_below_budget(self) -> Self:
        if self.overlap >= self.max_tokens:
            raise ValueError('overlap must be smaller than max_tokens')
        return self


class MarkdownOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')

    chunking: ChunkingOptions | None = Field(default=None, description='Structure-aware chunks, requires output=json')


class MarkdownRequest(OpRequest):
    output: OutputMode = OutputMode.JSON
    options: MarkdownOptions = Field(default_factory=MarkdownOptions)

    @model_validator(mode='after')
    def _chunking_requires_json(self) -> Self:
        if self.options.chunking is not None and self.output != OutputMode.JSON:
            raise ValueError('options.chunking requires output=json')
        return self


class Chunk(BaseModel):
    index: int
    kind: ChunkKind
    text: str
    language: CodeLanguage | None
    headings: list[str]
    slide: int | None
    start: int = Field(description='Offset of the chunk span in `markdown` (overlap not included)')
    end: int
    tokens: int


class MarkdownResult(BaseModel):
    markdown: str
    title: str | None
    source_type: SourceType
    chunks: list[Chunk] | None = Field(default=None, description='Present only when options.chunking is set')
