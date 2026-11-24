# Markdown Chunking

Optional structure-aware, token-bounded chunking of the markdown produced by `POST /v1/markdown`
(`adr/markdown.md`), for RAG ingestion. Uses the detected source type from `adr/mime.md`.

## Goals

- Chunks follow document structure: headings, tables, code; tables and code are never cut mid-row or mid-line.
- Code is split along syntax (function, class) using the detected or fenced language.
- Deterministic: same input and options give the same chunks.
- Zero network access at runtime; everything chonkie or tree-sitter would download is baked in at build.
- todo: `embedding` strategy (chonkie `SemanticChunker`) as an additive option once structural chunking has been
  validated on real data.

## Stack

| Concern    | Choice                                                                                 |
|------------|----------------------------------------------------------------------------------------|
| Library    | `chonkie[code]` 1.7 (`MarkdownChef`, `RecursiveChunker`, `TableChunker`, `CodeChunker`, `OverlapRefinery`) |
| Code AST   | `tree-sitter-language-pack` (pulled in by chonkie), curated grammars baked into the image |
| Tokenizer  | `tiktoken`, encodings baked into the image                                             |
| Placement  | Same markdown pool task as the conversion (one round trip), chonkie imported only in workers |

## Request

```jsonc
"options": {
  "chunking": {
    "maxTokens": 512,           // 64..8192, chunk size for every chunker
    "overlap": 0,               // < maxTokens, trailing context via OverlapRefinery, 0 = off
    "tokenizer": "o200k_base"   // Tokenizer: o200k_base | cl100k_base | word | character
  }
}
```

Value sets (`server/types/markdown.py`, `StrEnum`, exposed as OpenAPI enums):

| Enum           | Members                                                                         |
|----------------|---------------------------------------------------------------------------------|
| `Tokenizer`    | `O200K_BASE`, `CL100K_BASE`, `WORD`, `CHARACTER`                               |
| `ChunkKind`    | `TEXT`, `TABLE`, `CODE`                                                         |
| `CodeLanguage` | the curated grammars, values are tree-sitter names (`python`, `csharp`, `bash`) |

- `CodeLanguage` is the single source of truth for the grammars baked into the image; an alias map
  `dict[str, CodeLanguage]` in `services/chunking.py` resolves fence tags and magika labels (`py`, `cs`, `sh`,
  `shell`, `js`).

- Absent `chunking` means today's behavior.
- Chunks are a data section (`adr/api.md`): `output=file` returns and `destination` uploads the full JSON document
  as `<stem>.json`; the `destination` response carries no chunks.
- The tokenizer allowlist exists because arbitrary names make chonkie fetch models from the Hugging Face Hub.

## Response

The markdown response gains the `chunks` section, ordered by offset; empty markdown gives `[]`.

```jsonc
{ "source": { ... }, "options": { ... }, "result": { "content": "...", ... },
  "chunks": [
    { "index": 0, "kind": "text",                         // ChunkKind
      "text": "## Revenue\n\nRevenue grew 12% ...",
      "language": "python",                                // CodeLanguage, code only, omitted if not curated
      "headings": ["Quarterly Report", "Revenue"],        // heading path at chunk start
      "slide": 2,                                          // pptx, any presentation with readMode ocr
      "page": 3,                                           // readMode ocr only, omitted for presentations
      "start": 20, "end": 139, "tokens": 31 }             // offsets into result.content
  ] }
```

- todo: `page` for PDFs in `text` mode; markitdown's PDF converter emits no page markers today.

## Pipeline

```
markdown --MarkdownChef--+-- prose  --> RecursiveChunker (Papyrus rules)
                         +-- tables --> TableChunker (row splits, header repeated)
                         +-- code   --> CodeChunker (grammar available) | RecursiveChunker on lines (fallback)
         --> sort by offset --> headings / slide metadata --> OverlapRefinery (overlap > 0)
```

- **Prose rules** are defined in code (`RecursiveRules`), never loaded with `from_recipe`, which downloads from the
  Hugging Face Hub: level 1 markdown headings, then blank lines, then lines, then sentences, then whitespace.
- **Tables** are chunked with the request tokenizer and `maxTokens`, so every piece repeats the header row.
- **Code** blocks keep their fence in the chunk text (` ```python ... ``` `), so each chunk carries its language
  for embedding. Derived code sources (`adr/mime.md`) are one fenced block and go through the same path as code
  embedded in documents.
- **Language resolution:** fence tag or magika label -> `CodeLanguage` by value, then the alias map. Anything else
  is not curated: `language` is `null`, the fence tag stays in the chunk text and the line fallback is used.
  `CodeChunker` is only called for a `CodeLanguage` whose grammar is in `downloaded_languages()`; calling it for a
  missing grammar triggers a download of the whole 524 MB pack.
- **Sections:** prose is split at headings and slide markers before `RecursiveChunker` runs, so a chunk never spans
  two sections even when both would fit; heading-only runs (an H1 directly followed by an H2) join the next section.
- **Headings** path, **slide** and **page**: taken at the chunk's first body line (not a heading, marker or blank line), from
  ATX headings outside code blocks, so `#` comments in code never become headings.
- **Slides:** taken from `<!-- Slide number: N -->` markers (markitdown, `adr/ocr.md`).
- **Pages:** taken from the `<!-- Page number: N -->` markers of `adr/ocr.md`. Metadata only: unlike slide markers
  they never split a section, but they count as marker lines for heading-only runs and the first body line. A prose
  chunk with nothing but page markers (page starting with a table or code, blank pages) is dropped, so a blank
  document gives `[]`.
- **Overlap** is applied between prose chunks only, so fences and table rows are never cut; `start`/`end` always
  refer to the chunk's own span, the appended overlap is not included.
- **Tokens:** tiktoken encodings are wrapped in a subclass of chonkie's tiktoken adapter that uses
  `encode_ordinary`, so documents containing special token text (`<|endoftext|>`) are counted as plain text instead
  of crashing. `tokens` is the count of the final chunk text (fences and overlap included).

## Build And Runtime

- Docker build runs `python -m scripts.prefetch`, which reads the enums so build and code cannot
  drift: it downloads `[language.value for language in CodeLanguage]` into a fixed `cache_dir` via
  `tree_sitter_language_pack.download`, deletes the bundle archive, and runs `tiktoken.get_encoding` for every
  tiktoken-backed `Tokenizer` with `TIKTOKEN_CACHE_DIR` set to an image path. The app configures the same paths at
  worker start.
- Assets live under `PAPYRUS_ASSETS_DIR` (`/app/assets` in the image, gitignored `.assets/` in the repo for local
  development: run `uv run python -m scripts.prefetch` once); `chunking.configure()` must run before
  tree-sitter or tiktoken load anything, otherwise they fall back to user cache dirs. Prefetch is idempotent and
  fails if a curated grammar is still missing afterwards.
- Curated `CodeLanguage` members (25, about 62 MB with both encodings): python, javascript, typescript, tsx, java, kotlin, go,
  rust, c, cpp, csharp, php, ruby, swift, scala, bash, powershell, sql, yaml, toml, json, html, css, dockerfile, hcl.
  todo: make the grammar list configurable.
- Workers build the tiktoken encoding themselves and pass the object to chonkie; chonkie's own resolution of
  `o200k_base` goes through `tokie`, which downloads from the Hugging Face Hub.
- A test asserts that chunking runs with network access disabled for every allowed tokenizer and curated language;
  the image is smoke-tested with `docker run --network none`.

## Errors

| Case                                        | Code                     |
|---------------------------------------------|--------------------------|
| Option out of range or unknown tokenizer    | `422 validation_error`   |
| Chunking fails in the worker                | `500 processing_failed`  |
| Conversion plus chunking exceeds 120s       | `504 processing_timeout` |

## Out of Scope

- Embedding generation and vector store writes (chonkie handshakes); Papyrus only returns chunks.
- Per-request custom rules or recipes, arbitrary Hugging Face tokenizers.
