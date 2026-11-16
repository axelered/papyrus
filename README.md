# 📄 Papyrus

Axelered Papyrus is your single point stop for Agentic document processing.

## 📚 Supported Files

| Format     | Extensions             | Notes                                   |
|------------|------------------------|-----------------------------------------|
| PDF        | `.pdf`                 | Text layer only, scanned PDFs need OCR  |
| Word       | `.docx`                | Legacy `.doc` is not supported          |
| PowerPoint | `.pptx`                | Slide text, tables and notes            |
| Excel      | `.xlsx`, `.xls`        | One table per sheet                     |
| Outlook    | `.msg`                 | Headers and body                        |
| HTML       | `.html`, `.htm`        |                                         |
| CSV / TSV  | `.csv`, `.tsv`         | Rendered as a table                     |
| Jupyter    | `.ipynb`               |                                         |
| EPUB       | `.epub`                |                                         |
| RSS / Atom | `.rss`, `.atom`, `.xml`|                                         |
| ZIP        | `.zip`                 | Each entry converted recursively        |
| Data       | `.json`, `.jsonl`      | Passed through                          |
| Text       | `.txt`, `.md`, `.rst`, `.tex`, ... | Passed through              |
| Code       | `.py`, `.ts`, `.go`, `.sql`, `.yaml`, ... | Any text format magika knows |

The format is detected from the content (magika); the extension only refines generic matches such as `.csv` or
`.rss`. Mislabeled files are processed by their real type and reported in `source_type`.

## 🔌 API

All `/v1` routes require `Authorization: Bearer <key>`. Requests are either `application/json` or
`multipart/form-data`, where the `metadata` field carries the same JSON as a string and `file` is the upload.

| Field         | Description                                                                     |
|---------------|---------------------------------------------------------------------------------|
| `source`      | `file://<part>`, `http(s)://...` or `<protocol>://<connector>/<path>`; defaults to `file://file` on uploads |
| `destination` | Optional connector URI, the result is written there and a receipt is returned   |
| `output`      | `json` or `file`, default depends on the endpoint                               |

### Markdown

`POST /v1/markdown` extracts markdown from a document. `output` defaults to `json`.

```shell
curl -H "Authorization: Bearer <key>" -F file=@report.docx http://localhost:8000/v1/markdown
curl -H "Authorization: Bearer <key>" -H 'content-type: application/json' \
  -d '{"source": "s3://prod/docs/report.pdf", "destination": "s3://prod/md/report.md"}' \
  http://localhost:8000/v1/markdown
```

| Case          | Response                                                                              |
|---------------|---------------------------------------------------------------------------------------|
| `json`        | `{"markdown": "# Report ...", "title": "Quarterly", "source_type": {...}}`             |
| `file`        | `text/markdown`, filename `<source name>.md`                                          |
| `destination` | `{"destination": "s3://...", "content_type": "text/markdown; charset=utf-8", "size": 1234, "source_type": {...}}` |

`source_type` is the detected input: `{"mime_type": "text/html", "extension": "html", "label": "html", "group": "markup"}`.
For code, `label` is the language.

### Errors

Errors are `application/problem+json` with a stable `code`:

```json
{"type": "about:blank", "title": "Unsupported Media Type", "status": 415, "code": "unsupported_format", "detail": "..."}
```

| Status | Code                                           | Cause                                         |
|--------|------------------------------------------------|-----------------------------------------------|
| 401    | `unauthorized`                                 | Missing or invalid API key                    |
| 403    | `source_forbidden`                             | URL targets a private address or is not allowed |
| 404    | `source_not_found`                             | Source does not exist                         |
| 413    | `source_too_large`                             | Source exceeds `limits.max_file_size`         |
| 415    | `unsupported_format`, `unsupported_media_type` | Unsupported document or request body          |
| 422    | `validation_error`, `unsupported_uri`, `unknown_connector` | Invalid request, URI or connector |
| 502    | `source_fetch_failed`, `destination_failed`    | Remote source or destination failed           |
| 503    | `queue_full`                                   | Server busy, retry after `Retry-After`        |
| 504    | `processing_timeout`, `deadline_exceeded`      | Processing or request took too long           |

Interactive docs are served at `/docs`.

## 🧑🏼‍💻 Development

The server refuses to start without an API key. For local development create a gitignored `.env`:

```shell
PAPYRUS_AUTH_DISABLED=true
PAPYRUS_CONFIG=config.local.yml
```

```shell
uv run fastapi dev --entrypoint server:app --reload
```

Or run development environment with Dekit

```shell
dekit reload --yes
dekit up
```

Format and Lint

```shell
ruff format
ruff check --fix
```

Test

```shell
uv run pytest
```

## 🐳 Docker

```shell
docker build -t papyrus .
docker run -p 8000:8000 -e PAPYRUS_API_KEY=<key> -v ./config.yml:/etc/papyrus/config.yml:ro papyrus
```

Configuration reference: `adr/architecture.md`.