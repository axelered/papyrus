import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
import tree_sitter_language_pack as language_pack

from server.services import chunking
from server.services.chunking import available_languages, chunk_markdown, resolve_language
from server.services.detect import lookup
from server.types.markdown import ChunkingOptions, ChunkKind, CodeLanguage, Tokenizer

pytestmark = pytest.mark.usefixtures('chunking_assets')


REPORT = """# Quarterly Report

## Revenue

Revenue grew 12% year over year. The growth was driven by enterprise customers. Churn decreased slightly.

| Region | Q1 | Q2 | Q3 |
| --- | --- | --- | --- |
| EU | 10 | 11 | 12 |
| US | 20 | 21 | 22 |
| APAC | 5 | 6 | 7 |
| LATAM | 3 | 4 | 5 |

## Implementation

The pipeline is implemented in Python.

```python
import os


def load(path: str) -> bytes:
    # read the file
    with open(path, 'rb') as f:
        return f.read()


class Store:
    def __init__(self, root: str):
        self.root = root

    def put(self, key: str, data: bytes) -> None:
        with open(os.path.join(self.root, key), 'wb') as f:
            f.write(data)
```

### Notes

Final remarks here.
"""


def _options(**fields: object) -> ChunkingOptions:
    """Bypasses API bounds (max_tokens >= 64) so small budgets exercise splitting"""
    return ChunkingOptions.model_construct(**fields)


def _of(chunks: list, kind: ChunkKind) -> list:
    return [chunk for chunk in chunks if chunk.kind == kind]


@pytest.mark.parametrize('markdown', ['', '  \n\n '])
def test_empty_markdown_has_no_chunks(markdown: str):
    assert chunk_markdown(markdown, _options()) == []


def test_chunks_are_ordered_and_indexed():
    chunks = chunk_markdown(REPORT, _options(max_tokens=64))

    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert [chunk.start for chunk in chunks] == sorted(chunk.start for chunk in chunks)
    assert {chunk.kind for chunk in chunks} == set(ChunkKind)


def test_prose_offsets_point_into_markdown_and_carry_heading_path():
    chunks = _of(chunk_markdown(REPORT, _options(max_tokens=64)), ChunkKind.TEXT)

    assert all(REPORT[chunk.start : chunk.end] == chunk.text for chunk in chunks)
    revenue = next(chunk for chunk in chunks if 'Revenue grew' in chunk.text)
    notes = next(chunk for chunk in chunks if 'Final remarks' in chunk.text)
    assert revenue.headings == ['Quarterly Report', 'Revenue']
    assert notes.headings == ['Quarterly Report', 'Implementation', 'Notes']


def test_tables_split_by_rows_with_repeated_header():
    tables = _of(chunk_markdown(REPORT, _options(max_tokens=64)), ChunkKind.TABLE)

    assert len(tables) > 1
    assert all(table.text.startswith('| Region | Q1 | Q2 | Q3 |\n| --- |') for table in tables)
    assert all(table.tokens <= 64 for table in tables)
    assert tables[0].headings == ['Quarterly Report', 'Revenue']


def test_code_is_split_along_syntax_and_refenced():
    code = _of(chunk_markdown(REPORT, _options(max_tokens=64)), ChunkKind.CODE)

    assert len(code) > 1
    assert all(chunk.text.startswith('```python\n') and chunk.text.endswith('```\n') for chunk in code)
    assert all(chunk.language is CodeLanguage.PYTHON for chunk in code)
    assert any(chunk.text.startswith('```python\ndef load') or 'def load' in chunk.text for chunk in code)
    assert all(chunk.tokens <= 64 for chunk in code)


def test_code_comments_are_not_headings():
    chunks = chunk_markdown(REPORT, _options(max_tokens=64))

    assert all('read the file' not in chunk.headings for chunk in chunks)
    assert _of(chunks, ChunkKind.CODE)[0].headings == ['Quarterly Report', 'Implementation']


def test_uncurated_language_falls_back_to_lines():
    markdown = '```elixir\n' + ''.join(f'defmodule M{i} do\n  def f, do: {i}\nend\n\n' for i in range(30)) + '```\n'

    code = chunk_markdown(markdown, _options(max_tokens=64))

    assert len(code) > 1
    assert all(chunk.language is None and chunk.text.startswith('```elixir\n') for chunk in code)


def test_slide_numbers_from_markitdown_markers():
    markdown = '<!-- Slide number: 1 -->\n# Intro\n\nHello.\n\n<!-- Slide number: 2 -->\n# Roadmap\n\nShip it.\n'

    chunks = chunk_markdown(markdown, _options(max_tokens=64, tokenizer=Tokenizer.WORD))

    assert {chunk.text.strip().splitlines()[-1]: chunk.slide for chunk in chunks} == {'Hello.': 1, 'Ship it.': 2}


def _document(*pages: str) -> str:
    return '\n\n'.join(f'<!-- Page number: {number} -->\n\n{page}' for number, page in enumerate(pages, 1))


def test_page_is_where_the_chunk_body_starts():
    markdown = _document('# Intro\n\nHello.', '# Terms\n\nPay.', '# Annex')

    chunks = chunk_markdown(markdown, _options(max_tokens=64))

    assert [(chunk.page, chunk.headings[-1]) for chunk in chunks if 'Annex' not in chunk.headings] == [
        (1, 'Intro'),
        (2, 'Terms'),
    ]
    assert all(chunk.slide is None for chunk in chunks)


def test_paragraph_continues_across_pages():
    chunks = chunk_markdown(_document('The contract', 'ends in May.'), _options(max_tokens=64))

    assert [chunk.page for chunk in chunks] == [1]
    assert 'ends in May.' in chunks[0].text


def test_page_split_by_the_budget_keeps_its_number():
    chunks = chunk_markdown(_document('alpha beta. ' * 60, 'gamma delta. ' * 60), _options(max_tokens=32))

    assert {chunk.page for chunk in chunks if 'alpha' in chunk.text} == {1}
    assert {chunk.page for chunk in chunks if 'gamma' in chunk.text and 'alpha' not in chunk.text} == {2}


@pytest.mark.parametrize(
    ('body', 'kind'),
    [('| a | b |\n| --- | --- |\n| 1 | 2 |', ChunkKind.TABLE), ('```python\nx = 1\n```', ChunkKind.CODE)],
)
def test_page_marker_alone_is_not_a_chunk(body: str, kind: ChunkKind):
    chunks = chunk_markdown(_document('Intro.', body, body, 'End.'), _options(max_tokens=64))

    expected = [(ChunkKind.TEXT, 1), (kind, 2), (kind, 3), (ChunkKind.TEXT, 4)]
    assert [(chunk.kind, chunk.page) for chunk in chunks] == expected


def test_blank_pages_give_no_chunks():
    assert chunk_markdown(_document('', '', ''), _options(max_tokens=64)) == []


def test_no_page_without_markers():
    assert all(chunk.page is None for chunk in chunk_markdown(REPORT, _options(max_tokens=64)))


def test_special_token_text_is_plain_text():
    chunks = chunk_markdown('# Prompt\n\nUse <|endoftext|> as separator.\n', _options())

    assert '<|endoftext|>' in chunks[0].text


def test_overlap_appends_next_prose():
    plain = _of(chunk_markdown(REPORT, _options(max_tokens=32)), ChunkKind.TEXT)
    overlapped = _of(chunk_markdown(REPORT, _options(max_tokens=32, overlap=4)), ChunkKind.TEXT)

    assert [chunk.start for chunk in overlapped] == [chunk.start for chunk in plain]
    assert overlapped[0].text.startswith(plain[0].text)
    assert len(overlapped[0].text) > len(plain[0].text)


@pytest.mark.parametrize('tokenizer', list(Tokenizer))
def test_every_tokenizer_respects_budget(tokenizer: Tokenizer):
    chunks = chunk_markdown(REPORT, _options(max_tokens=80, tokenizer=tokenizer))

    assert chunks
    assert all(chunk.tokens <= 80 for chunk in chunks if chunk.kind != ChunkKind.CODE)


def test_runs_without_network(monkeypatch: pytest.MonkeyPatch):
    import tiktoken.registry
    import tree_sitter_language_pack as language_pack

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError('network access attempted')

    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    monkeypatch.setattr(tiktoken.registry, 'ENCODINGS', {})
    chunking._tokenizer.cache_clear()

    assert available_languages() == frozenset(CodeLanguage)
    for language in CodeLanguage:
        language_pack.get_parser(language.value)
    for tokenizer in Tokenizer:
        assert chunk_markdown(REPORT, _options(max_tokens=64, tokenizer=tokenizer))
    chunking._tokenizer.cache_clear()


def test_sections_are_never_merged_even_with_room():
    texts = _of(chunk_markdown(REPORT, _options(max_tokens=4096)), ChunkKind.TEXT)

    assert [chunk.headings for chunk in texts] == [
        ['Quarterly Report', 'Revenue'],
        ['Quarterly Report', 'Implementation'],
        ['Quarterly Report', 'Implementation', 'Notes'],
    ]
    assert texts[0].text.startswith('# Quarterly Report\n\n## Revenue\n\nRevenue grew')


def test_aliases_never_shadow_language_values():
    assert set(chunking.LANGUAGE_ALIASES).isdisjoint(language.value for language in CodeLanguage)


@pytest.mark.parametrize(
    ('tag', 'language'),
    [
        ('python', CodeLanguage.PYTHON),
        (' Py ', CodeLanguage.PYTHON),
        ('cs', CodeLanguage.CSHARP),
        ('shell', CodeLanguage.BASH),
        ('tf', CodeLanguage.HCL),
        ('elixir', None),
        ('', None),
        (None, None),
    ],
)
def test_resolve_language(tag: str | None, language: CodeLanguage | None):
    assert resolve_language(tag) is language


@pytest.mark.parametrize('label', ['python', 'javascript', 'typescript', 'cs', 'shell', 'go', 'rust', 'sql', 'yaml'])
def test_magika_code_labels_resolve(label: str):
    assert lookup(label).derived
    assert resolve_language(label) is not None


@pytest.fixture
def isolated_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv(chunking.ASSETS_ENV, str(tmp_path))
    monkeypatch.setenv('TIKTOKEN_CACHE_DIR', '')
    yield tmp_path
    monkeypatch.undo()
    chunking.configure()


def test_configure_points_libraries_at_assets(isolated_assets: Path):
    chunking.configure()

    assert os.environ['TIKTOKEN_CACHE_DIR'] == str(isolated_assets / 'tiktoken')
    assert language_pack.downloaded_languages() == []
