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
    "max_tokens": 512,          // 64..8192, chunk size for every chunker
    "overlap": 0,               // < max_tokens, trailing context via OverlapRefinery, 0 = off
    "tokenizer": "o200k_base"   // o200k_base | cl100k_base | word | character
  }
}
```

- Absent `chunking` means today's behavior.
- Chunking requires `output: json`; any other output is `422 validation_error`. With `destination`, the full JSON
  body is written as `<stem>.json` and the receipt reports `application/json`.
- The tokenizer allowlist exists because arbitrary names make chonkie fetch models from the Hugging Face Hub.

## Response

The markdown JSON result gains `chunks`, ordered by offset; empty markdown gives `[]`.

```jsonc
{ "markdown": "...", "title": "...", "source_type": { ... },
  "chunks": [
    { "index": 0, "kind": "text",                         // text | table | code
      "text": "## Revenue\n\nRevenue grew 12% ...",
      "language": null,                                    // code only, tree-sitter name
      "headings": ["Quarterly Report", "Revenue"],        // heading path at chunk start
      "slide": null,                                       // pptx only
      "start": 20, "end": 139, "tokens": 31 }             // offsets into "markdown"
  ] }
```

- todo: `page` for PDFs; markitdown's PDF converter emits no page markers today.

## Pipeline

```
markdown --MarkdownChef--+-- prose  --> RecursiveChunker (Papyrus rules)
                         +-- tables --> TableChunker (row splits, header repeated)
                         +-- code   --> CodeChunker (grammar available) | RecursiveChunker on lines (fallback)
         --> sort by offset --> headings / slide metadata --> OverlapRefinery (overlap > 0)
```

- **Prose rules** are defined in code (`RecursiveRules`), never loaded with `from_recipe`, which downloads from the
  Hugging Face Hub: level 1 markdown headings, then blank lines, then lines, then sentences, then whitespace.
- **Tables** are chunked with the request tokenizer and `max_tokens`, so every piece repeats the header row.
- **Code** blocks keep their fence in the chunk text (` ```python ... ``` `), so each chunk carries its language
  for embedding. Derived code sources (`adr/mime.md`) are one fenced block and go through the same path as code
  embedded in documents.
- **Language resolution:** fence tag or magika label -> tree-sitter name through a small alias map (`cs` -> `csharp`,
  `sh`/`shell` -> `bash`, `js` -> `javascript`, `py` -> `python`), else `detect_language_from_extension`. If the
  grammar is not in `downloaded_languages()` the line fallback is used; `CodeChunker` is never called for a missing
  grammar because that triggers a download of the whole 524 MB pack.
- **Headings** path: computed from the heading lines preceding each chunk's `start`.
- **Slides:** taken from markitdown's `<!-- Slide number: N -->` markers.

## Build And Runtime

- Docker build prefetches the curated grammars into a fixed `cache_dir` via `tree_sitter_language_pack.download`,
  deletes the downloaded bundle archive, and the app configures the same `cache_dir` at worker start.
- Curated grammars (about 25, about 80 MB): python, javascript, typescript, tsx, java, kotlin, go, rust, c, cpp,
  csharp, php, ruby, swift, scala, bash, powershell, sql, yaml, toml, json, html, css, dockerfile, hcl.
  todo: make the grammar list configurable.
- Docker build runs `tiktoken.get_encoding` for each allowed encoding with `TIKTOKEN_CACHE_DIR` set to an image path.
  Workers build the tiktoken encoding themselves and pass the object to chonkie; chonkie's own resolution of
  `o200k_base` goes through `tokie`, which downloads from the Hugging Face Hub.
- A test asserts that chunking runs with network access disabled for every allowed tokenizer and curated language.

## Errors

| Case                                        | Code                     |
|---------------------------------------------|--------------------------|
| `chunking` with `output` other than `json`  | `422 validation_error`   |
| Option out of range or unknown tokenizer    | `422 validation_error`   |
| Chunking fails in the worker                | `500 processing_failed`  |
| Conversion plus chunking exceeds 120s       | `504 processing_timeout` |

## Out of Scope

- Embedding generation and vector store writes (chonkie handshakes); Papyrus only returns chunks.
- Per-request custom rules or recipes, arbitrary Hugging Face tokenizers.
