# Markdown Extraction

`POST /v1/markdown` extracts markdown from a document with markitdown. Follows the request, output, error and
service conventions of `adr/architecture.md`.

## Contract

- Request: the common `OpRequest` fields plus `options.chunking` (optional, see `adr/chunking.md`).
- `output` defaults to `json`. With `options.chunking`, `output` must be `json` and the JSON result gains `chunks`.

| Output        | Response                                                                            |
|---------------|-------------------------------------------------------------------------------------|
| `json`        | `{"markdown": str, "title": str \| null, "source_type": {"mime_type", "extension", "label", "group"}}` |
| `file`        | `text/markdown; charset=utf-8`, filename `<source stem>.md`                         |
| `destination` | Markdown file written to the connector URI, receipt `{destination, content_type, size, source_type}` |

```shell
curl -H "Authorization: Bearer $KEY" -F file=@report.docx http://localhost:8000/v1/markdown
curl -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"source": "s3://prod/docs/report.pdf", "destination": "s3://prod/md/report.md"}' \
  http://localhost:8000/v1/markdown
```

## Service

- `MarkdownService` (pool kind), worker `convert_to_markdown(source, target) -> title`.
- One `MarkItDown(enable_plugins=False)` per worker process, created on first task.
- Markdown is written with `\n` newlines regardless of platform.
- Sizing heuristics: `processes = workers.processes`, timeout 120s, `max_tasks` 100.
- Formats: markitdown built-ins plus the `pdf,docx,pptx,xlsx,xls,outlook` extras.
- Allowlist (format labels from `adr/mime.md`): `pdf`, `docx`, `pptx`, `xlsx`, `xls`, `outlook`, `html`, `csv`,
  `tsv`, `markdown`, `txt`, `json`, `jsonl`, `xml`, `rss`, `atom`, `ipynb`, `epub`, `zip`, plus every derived text
  format (`MARKDOWN_FORMATS` and `accepts` in `api/markdown.py`).
- `doc`, `rtf`, `odt` (to `docx`), `ppt`, `odp` (to `pptx`) and `ods` (to `xlsx`) are converted with LibreOffice
  first (`CONVERSIONS`, `adr/convert.md`).
  The source is detected and its extension normalized before the worker runs, so markitdown always receives a
  correct extension.
- todo: parse `eml` with the stdlib `email` package (headers, text body, attachment names); raw text until then.

### Code

- Derived formats with group `code` skip markitdown: a second worker function (`fence_code`, same pool) decodes the
  file and wraps it in one fenced block tagged with the label (```` ```python ````). Decoding tries strict UTF-8
  (BOM aware) first and uses `charset_normalizer` only otherwise, since short single-byte texts are ambiguous
  between code pages. Line endings are normalized to `\n`.
- The fence length is `max(3, longest backtick run in the content + 1)` so embedded fences cannot break out.
- Rationale: markitdown passes code through as raw text, where `#` comments become markdown headings and corrupt
  heading-based chunking.
- Explicit formats grouped as code (`ipynb`) still go through markitdown.
- `title` is `null` for code.

## Errors

| Case                                                  | Code                        |
|-------------------------------------------------------|-----------------------------|
| Detected format not in the allowlist (before worker)  | `415 unsupported_format`    |
| No converter accepts the file                         | `415 unsupported_format`    |
| Converter accepted but failed (corrupt, mislabeled)   | `415 unsupported_format`    |
| Unexpected worker error or crash                      | `500 processing_failed`     |
| Exceeds 120s                                          | `504 processing_timeout`    |

## Out of Scope

- markitdown plugins, LLM image descriptions, audio transcription, Azure Document Intelligence.
- todo: OCR for scanned PDFs (produces empty markdown today), via the future OCR service.
