import bisect
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from server.types.markdown import Chunk, ChunkingOptions, ChunkKind, CodeLanguage, Tokenizer
from server.utils.text import fence

ASSETS_ENV = 'PAPYRUS_ASSETS_DIR'
_DEFAULT_ASSETS_DIR = Path(__file__).resolve().parents[2] / '.assets'


def assets_dir() -> Path:
    return Path(os.environ.get(ASSETS_ENV) or _DEFAULT_ASSETS_DIR)


def configure() -> None:
    """Points tree-sitter and tiktoken at the prefetched assets; must run before either library loads anything"""
    import tree_sitter_language_pack as language_pack
    from tree_sitter_language_pack.options import PackConfig

    root = assets_dir()
    os.environ['TIKTOKEN_CACHE_DIR'] = str(root / 'tiktoken')
    language_pack.configure(PackConfig(cache_dir=str(root / 'tree-sitter')))


def available_languages() -> frozenset[CodeLanguage]:
    import tree_sitter_language_pack as language_pack

    downloaded = set(language_pack.downloaded_languages())
    return frozenset(language for language in CodeLanguage if language.value in downloaded)


# fence tags and magika labels that name a curated language differently
LANGUAGE_ALIASES: dict[str, CodeLanguage] = {
    'py': CodeLanguage.PYTHON,
    'python3': CodeLanguage.PYTHON,
    'js': CodeLanguage.JAVASCRIPT,
    'jsx': CodeLanguage.JAVASCRIPT,
    'mjs': CodeLanguage.JAVASCRIPT,
    'cjs': CodeLanguage.JAVASCRIPT,
    'node': CodeLanguage.JAVASCRIPT,
    'ts': CodeLanguage.TYPESCRIPT,
    'kt': CodeLanguage.KOTLIN,
    'kts': CodeLanguage.KOTLIN,
    'golang': CodeLanguage.GO,
    'rs': CodeLanguage.RUST,
    'h': CodeLanguage.C,
    'c++': CodeLanguage.CPP,
    'cc': CodeLanguage.CPP,
    'cxx': CodeLanguage.CPP,
    'hpp': CodeLanguage.CPP,
    'cs': CodeLanguage.CSHARP,
    'c#': CodeLanguage.CSHARP,
    'rb': CodeLanguage.RUBY,
    'sh': CodeLanguage.BASH,
    'shell': CodeLanguage.BASH,
    'zsh': CodeLanguage.BASH,
    'console': CodeLanguage.BASH,
    'ps1': CodeLanguage.POWERSHELL,
    'pwsh': CodeLanguage.POWERSHELL,
    'postgresql': CodeLanguage.SQL,
    'mysql': CodeLanguage.SQL,
    'yml': CodeLanguage.YAML,
    'jsonc': CodeLanguage.JSON,
    'htm': CodeLanguage.HTML,
    'docker': CodeLanguage.DOCKERFILE,
    'terraform': CodeLanguage.HCL,
    'tf': CodeLanguage.HCL,
}


def resolve_language(tag: str | None) -> CodeLanguage | None:
    """Fence tag or magika label to a curated language; anything else is not AST-chunked"""
    if not tag:
        return None
    key = tag.strip().lower()
    if key in CodeLanguage:
        return CodeLanguage(key)
    return LANGUAGE_ALIASES.get(key)


_HEADING = re.compile(r'^(#{1,6})[ \t]+(.+?)[ \t#]*$', re.MULTILINE)
_SLIDE = re.compile(r'<!--\s*Slide number:\s*(\d+)\s*-->')
_PAGE = re.compile(r'<!--\s*Page number:\s*(\d+)\s*-->')
_MIN_CODE_BUDGET = 16


@dataclass
class _Piece:
    start: int
    end: int
    kind: ChunkKind
    text: str
    language: CodeLanguage | None = None


@cache
def _tokenizer(name: Tokenizer) -> Any:
    from chonkie import AutoTokenizer

    if not name.is_tiktoken:
        return AutoTokenizer(name.value)

    import tiktoken
    from chonkie.tokenizer import TiktokenAutoTokenizer

    class OrdinaryTiktoken(TiktokenAutoTokenizer):
        """Special tokens are plain text, so documents containing '<|endoftext|>' do not crash tiktoken"""

        def encode(self, text: str) -> Sequence[int]:
            return self.tokenizer.encode_ordinary(text)

        def encode_batch(self, texts: Sequence[str]) -> Sequence[Sequence[int]]:
            return self.tokenizer.encode_ordinary_batch(list(texts))

        def count_tokens(self, text: str) -> int:
            return len(self.encode(text))

        def count_tokens_batch(self, texts: Sequence[str]) -> Sequence[int]:
            return [len(tokens) for tokens in self.encode_batch(texts)]

    # an explicit Encoding object, chonkie's own name resolution goes through tokie and the Hugging Face Hub
    return OrdinaryTiktoken(tiktoken.get_encoding(name.value))


@cache
def _rules() -> tuple[Any, Any]:
    from chonkie.types import RecursiveLevel, RecursiveRules

    headings = [f'\n{"#" * level} ' for level in range(1, 7)]
    prose = RecursiveRules(
        levels=[
            RecursiveLevel(delimiters=headings, include_delim='next'),
            RecursiveLevel(delimiters=['\n\n']),
            RecursiveLevel(delimiters=['\n']),
            RecursiveLevel(delimiters=['. ', '! ', '? ']),
            RecursiveLevel(whitespace=True),
        ]
    )
    lines = RecursiveRules(
        levels=[RecursiveLevel(delimiters=['\n\n']), RecursiveLevel(delimiters=['\n']), RecursiveLevel(whitespace=True)]
    )
    return prose, lines


def chunk_markdown(markdown: str, options: ChunkingOptions) -> list[Chunk]:
    """Structure-aware chunks of `markdown`, ordered by offset"""
    if not markdown.strip():
        return []
    configure()
    from chonkie import MarkdownChef

    tokenizer = _tokenizer(options.tokenizer)
    document = MarkdownChef().parse(markdown)
    pieces = [
        *_prose(markdown, document.chunks, tokenizer, options),
        *_tables(markdown, document.tables, tokenizer, options),
        *_code(markdown, document.code, tokenizer, options),
    ]
    pieces.sort(key=lambda piece: piece.start)
    if options.overlap:
        _add_overlap([piece for piece in pieces if piece.kind == ChunkKind.TEXT], tokenizer, options.overlap)
    return _finalize(markdown, pieces, document.code, tokenizer)


def _prose(markdown: str, segments: list[Any], tokenizer: Any, options: ChunkingOptions) -> list[_Piece]:
    from chonkie import RecursiveChunker

    chunker = RecursiveChunker(tokenizer=tokenizer, chunk_size=options.max_tokens, rules=_rules()[0])
    pieces = []
    for segment in segments:
        for offset, section in _sections(segment.text):
            for chunk in chunker.chunk(section):
                # a page starting with a table or code leaves its marker alone in the prose
                if _PAGE.sub('', chunk.text).strip():
                    start = segment.start_index + offset + chunk.start_index
                    pieces.append(_Piece(start, start + len(chunk.text), ChunkKind.TEXT, chunk.text))
    return pieces


def _sections(text: str) -> list[tuple[int, str]]:
    """Splits at headings and slide markers so chunks never span two sections; heading-only runs join the next"""
    boundaries = sorted({0, *(m.start() for m in _HEADING.finditer(text)), *(m.start() for m in _SLIDE.finditer(text))})
    sections = [(start, text[start:end]) for start, end in zip(boundaries, [*boundaries[1:], len(text)], strict=True)]
    merged: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    for start, section in sections:
        if pending is not None:
            start, section = pending[0], pending[1] + section
            pending = None
        if _is_heading_only(section):
            pending = (start, section)
        elif section.strip():
            merged.append((start, section))
    if pending is not None:
        merged.append(pending)
    return merged


def _is_heading_only(section: str) -> bool:
    return all(not line.strip() or _is_marker(line) for line in section.splitlines())


def _is_marker(line: str) -> bool:
    return any(pattern.fullmatch(line.strip()) for pattern in (_HEADING, _SLIDE, _PAGE))


def _body_offset(text: str) -> int:
    """Offset of the first line that is neither blank, a heading nor a marker (0 for heading-only text)"""
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.strip() and not _is_marker(line):
            return offset
        offset += len(line)
    return 0


def _tables(markdown: str, tables: list[Any], tokenizer: Any, options: ChunkingOptions) -> list[_Piece]:
    from chonkie import TableChunker

    chunker = TableChunker(tokenizer=tokenizer, chunk_size=options.max_tokens)
    pieces = []
    for table in tables:
        offset = _content_offset(markdown, table.content, table.start_index)
        for chunk in chunker.chunk(table.content):
            pieces.append(_Piece(offset + chunk.start_index, offset + chunk.end_index, ChunkKind.TABLE, chunk.text))
    return pieces


def _code(markdown: str, blocks: list[Any], tokenizer: Any, options: ChunkingOptions) -> list[_Piece]:
    from chonkie import CodeChunker, RecursiveChunker

    available = available_languages()
    pieces = []
    for block in blocks:
        tag = block.language or ''
        language = resolve_language(tag)
        overhead = tokenizer.count_tokens(fence('', tag))
        budget = max(_MIN_CODE_BUDGET, options.max_tokens - overhead)
        if language in available:
            chunker = CodeChunker(tokenizer=tokenizer, chunk_size=budget, language=language.value)
        else:
            # never call CodeChunker for a missing grammar, it would download the whole language pack
            chunker = RecursiveChunker(tokenizer=tokenizer, chunk_size=budget, rules=_rules()[1])
        offset = _content_offset(markdown, block.content, block.start_index)
        for chunk in chunker.chunk(block.content) if block.content.strip() else []:
            if chunk.text.strip():
                text = fence(chunk.text, tag)
                start, end = offset + chunk.start_index, offset + chunk.end_index
                pieces.append(_Piece(start, end, ChunkKind.CODE, text, language))
    return pieces


def _content_offset(markdown: str, content: str, start: int) -> int:
    found = markdown.find(content, start)
    return found if found >= 0 else start


def _add_overlap(pieces: list[_Piece], tokenizer: Any, overlap: int) -> None:
    """Appends the start of the next prose chunk; offsets keep pointing at the chunk's own span"""
    from chonkie import Chunk, OverlapRefinery

    chunks = [Chunk(text=piece.text, start_index=piece.start, end_index=piece.end) for piece in pieces]
    refined = OverlapRefinery(tokenizer=tokenizer, context_size=overlap, method='suffix', merge=True)(chunks)
    for piece, chunk in zip(pieces, refined, strict=True):
        piece.text = chunk.text


def _finalize(markdown: str, pieces: list[_Piece], blocks: list[Any], tokenizer: Any) -> list[Chunk]:
    headings = _headings(markdown, [(block.start_index, block.end_index) for block in blocks])
    heading_offsets = [offset for offset, _, _ in headings]
    slides, pages = _numbers(_SLIDE, markdown), _numbers(_PAGE, markdown)
    chunks = []
    for index, piece in enumerate(pieces):
        anchor = piece.start + _body_offset(piece.text) if piece.kind == ChunkKind.TEXT else piece.start
        chunks.append(
            Chunk(
                index=index,
                kind=piece.kind,
                text=piece.text,
                language=piece.language,
                headings=_heading_path(headings[: bisect.bisect_right(heading_offsets, anchor)]),
                slide=_number_at(slides, anchor),
                page=_number_at(pages, anchor),
                start=piece.start,
                end=piece.end,
                tokens=tokenizer.count_tokens(piece.text),
            )
        )
    return chunks


def _numbers(marker: re.Pattern[str], markdown: str) -> list[tuple[int, int]]:
    return [(match.start(), int(match.group(1))) for match in marker.finditer(markdown)]


def _number_at(numbers: list[tuple[int, int]], offset: int) -> int | None:
    position = bisect.bisect_right(numbers, offset, key=lambda number: number[0])
    return numbers[position - 1][1] if position else None


def _headings(markdown: str, code_spans: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    """ATX headings outside code blocks, so '#' comments in code never become headings"""
    found = []
    for match in _HEADING.finditer(markdown):
        if not any(start <= match.start() < end for start, end in code_spans):
            found.append((match.start(), len(match.group(1)), match.group(2).strip()))
    return found


def _heading_path(headings: list[tuple[int, int, str]]) -> list[str]:
    path: list[tuple[int, str]] = []
    for _, level, title in headings:
        while path and path[-1][0] >= level:
            path.pop()
        path.append((level, title))
    return [title for _, title in path]
