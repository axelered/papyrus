# Markdown Extraction

`POST /v1/markdown` extracts markdown from a document with markitdown. Follows the request, output, error and
service conventions of `adr/architecture.md`.

## Contract

- Request: the common `OpRequest` fields only; no `options` yet.
- `output` defaults to `json`.

| Output        | Response                                                                            |
|---------------|-------------------------------------------------------------------------------------|
| `json`        | `{"markdown": str, "title": str \| null}`                                           |
| `file`        | `text/markdown; charset=utf-8`, filename `<source stem>.md`                         |
| `destination` | Markdown file written to the connector URI, receipt `{destination, content_type, size}` |

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
- Formats: markitdown built-ins (html, csv, json, xml, txt, zip, epub, ...) plus the `pdf,docx,pptx,xlsx,xls,outlook`
  extras. Format detection uses the source extension plus magika content sniffing.

## Errors

| Case                                                  | Code                        |
|-------------------------------------------------------|-----------------------------|
| No converter accepts the file                         | `415 unsupported_format`    |
| Converter accepted but failed (corrupt, mislabeled)   | `415 unsupported_format`    |
| Unexpected worker error or crash                      | `500 processing_failed`     |
| Exceeds 120s                                          | `504 processing_timeout`    |

## Out of Scope

- markitdown plugins, LLM image descriptions, audio transcription, Azure Document Intelligence.
- todo: OCR for scanned PDFs (produces empty markdown today), via the future OCR service.
