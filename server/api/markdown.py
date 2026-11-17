import json
from typing import Self

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.api.output import Receipt, SourceType, respond
from server.api.request import OpRequest, OutputMode, op_context, openapi_body
from server.auth import require_api_key
from server.errors import UnsupportedFormat
from server.formats import Format, FormatGroup, FormatLabel
from server.services.chunking.chunker import ChunkOptions
from server.services.chunking.types import ChunkKind, CodeLanguage, Tokenizer
from server.utils.openapi import inline_refs

MEDIA_TYPE = 'text/markdown; charset=utf-8'
JSON_MEDIA_TYPE = 'application/json'

MARKDOWN_FORMATS = frozenset(
    {
        FormatLabel.PDF,
        FormatLabel.DOCX,
        FormatLabel.PPTX,
        FormatLabel.XLSX,
        FormatLabel.XLS,
        FormatLabel.OUTLOOK,
        FormatLabel.HTML,
        FormatLabel.CSV,
        FormatLabel.TSV,
        FormatLabel.MARKDOWN,
        FormatLabel.TXT,
        FormatLabel.JSON,
        FormatLabel.JSONL,
        FormatLabel.XML,
        FormatLabel.RSS,
        FormatLabel.ATOM,
        FormatLabel.IPYNB,
        FormatLabel.EPUB,
        FormatLabel.ZIP,
    }
)

router = APIRouter(prefix='/v1', tags=['markdown'], dependencies=[Depends(require_api_key)])


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

    def to_options(self) -> ChunkOptions:
        return ChunkOptions(max_tokens=self.max_tokens, overlap=self.overlap, tokenizer=self.tokenizer)


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


def accepts(value: Format) -> bool:
    """Explicit allowlist plus every format derived from magika that is text (code, configs, markup)"""
    return value.label in MARKDOWN_FORMATS or (value.derived and value.text)


def is_code(value: Format) -> bool:
    """Only derived code is fenced; explicit formats grouped as code (ipynb) have a dedicated markitdown converter"""
    return value.derived and value.group == FormatGroup.CODE


@router.post(
    '/markdown',
    summary='Extract markdown from a document',
    openapi_extra=openapi_body(MarkdownRequest),
    response_model=None,
    responses={
        200: {
            'description': 'Markdown as JSON, as a file, or a receipt when `destination` is set',
            'content': {
                'application/json': {
                    'schema': {
                        'anyOf': [
                            inline_refs(MarkdownResult.model_json_schema()),
                            inline_refs(Receipt.model_json_schema()),
                        ]
                    }
                },
                'text/markdown': {'schema': {'type': 'string'}},
            },
        }
    },
)
async def markdown(request: Request) -> Response:
    services = request.app.state.services
    async with op_context(request, MarkdownRequest) as op:
        source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
        document = await services.detect.detect(source)
        if not accepts(document.format):
            raise UnsupportedFormat(
                f'{document.format.mime_type} ({document.format.label}) is not supported for markdown extraction'
            )
        stem = document.path.stem
        target = op.workspace.new_file(f'{stem}.md')
        chunking = op.model.options.chunking.to_options() if op.model.options.chunking else None
        chunks_target = op.workspace.new_file(f'{stem}.chunks.json') if chunking else None
        if is_code(document.format):
            title = None
            await services.markdown.fence_code(document.path, target, document.format.label, chunking, chunks_target)
        else:
            title = await services.markdown.convert(document.path, target, chunking, chunks_target)

        source_type = SourceType.of(document.format)
        result = MarkdownResult(markdown=target.read_text(encoding='utf-8'), title=title, source_type=source_type)
        if chunks_target is None:
            return await respond(request, op, target, MEDIA_TYPE, lambda: result, source_type)

        result.chunks = [Chunk.model_validate(chunk) for chunk in json.loads(chunks_target.read_text('utf-8'))]
        body = op.workspace.new_file(f'{stem}.json')
        body.write_text(result.model_dump_json(), encoding='utf-8')
        return await respond(request, op, body, JSON_MEDIA_TYPE, lambda: result, source_type)
