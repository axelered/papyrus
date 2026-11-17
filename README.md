# 📄 Papyrus

Axelered Papyrus is your single point stop for Agentic document processing.

## 📚 Supported Files

| Format     | Extensions             | Notes                                   |
|------------|------------------------|-----------------------------------------|
| PDF        | `.pdf`                 | Text layer only, scanned PDFs need OCR  |
| Word       | `.docx`, `.doc`, `.rtf` | Legacy formats converted with LibreOffice |
| PowerPoint | `.pptx`, `.ppt`        | Slide text, tables and notes            |
| Excel      | `.xlsx`, `.xls`        | One table per sheet                     |
| OpenDocument | `.odt`, `.odp`, `.ods` | Converted with LibreOffice            |
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
| Images     | `.png`, `.jpg`, `.webp`, `.gif`, `.bmp`, `.tiff`, `.avif` | Thumbnail and render only |

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

#### Chunking

Optional structure-aware chunks for RAG, requires `output: json`. Chunks follow headings, split tables by rows with the
header repeated, and split code along functions and classes.

```json
{"source": "s3://prod/docs/report.docx", "options": {"chunking": {"max_tokens": 512, "overlap": 0, "tokenizer": "o200k_base"}}}
```

| Option       | Default      | Values                                          |
|--------------|--------------|-------------------------------------------------|
| `max_tokens` | `512`        | `64` to `8192`                                  |
| `overlap`    | `0`          | tokens of the next prose chunk, below `max_tokens` |
| `tokenizer`  | `o200k_base` | `o200k_base`, `cl100k_base`, `word`, `character` |

The result gains `chunks`:

```json
{"index": 2, "kind": "code", "text": "```python\ndef load(path): ...\n```\n", "language": "python",
 "headings": ["Report", "Implementation"], "slide": null, "start": 278, "end": 412, "tokens": 87}
```

`kind` is `text`, `table` or `code`; `start`/`end` are offsets into `markdown`; `slide` is set for presentations.

### Thumbnail and Render

`POST /v1/thumbnail` fits one page or image into a box, `POST /v1/render` renders it at a readable size. Both accept
PDF, images, Office and OpenDocument files and HTML, and return a file by default. For images, `page` selects the
frame of a GIF, TIFF or animated WebP. Office and HTML pages are exported one at a time with LibreOffice, so their
result has no `page_count`.

```shell
curl -H "Authorization: Bearer <key>" -F file=@report.pdf -o report.webp http://localhost:8000/v1/thumbnail
curl -H "Authorization: Bearer <key>" -F file=@report.pdf -F 'metadata={"options": {"page": 3, "dpi": 200}}' \
  -o report-3.png http://localhost:8000/v1/render
```

| Thumbnail option | Default   | Values                                        |
|------------------|-----------|-----------------------------------------------|
| `page`           | `1`       | page or frame                                 |
| `width`          | `256`     | box width, `1` to `4096`                      |
| `height`         | `256`     | box height, `1` to `4096`                     |
| `fit`            | `contain` | `contain` keeps the whole page, `cover` fills the box and crops the center |
| `format`         | `webp`    | `png`, `jpeg`, `webp`                         |

| Render option | Default             | Values                                                        |
|---------------|---------------------|---------------------------------------------------------------|
| `page`        | `1`                 | page or frame                                                 |
| `width`       | none                | `1` to `4096`, the height follows the aspect ratio            |
| `height`      | none                | `1` to `4096`; with `width` the page fits inside the box      |
| `dpi`         | `150` when no size  | `1` to `600`, not combined with `width`/`height`; images ignore it |
| `format`      | `png`               | `png`, `jpeg`, `webp`                                         |

Images are never enlarged, and every output is at most 4096 px per side. `json` returns
`{"data": "<base64>", "content_type": "image/png", "width": 1240, "height": 1755, "page": 3, "page_count": 12, "source_type": {...}}`.

### Screenshot

`POST /v1/screenshot` loads a web page in headless Chromium and captures it. `source` must be an `http(s)://` URL.
`output` defaults to `file`.

```shell
curl -H "Authorization: Bearer <key>" -H 'content-type: application/json' \
  -d '{"source": "https://example.com", "options": {"full_page": true, "format": "webp"}}' \
  -o example.webp http://localhost:8000/v1/screenshot
```

| Option       | Default | Values                                                    |
|--------------|---------|-----------------------------------------------------------|
| `width`      | `1280`  | viewport width, `320` to `3840`                           |
| `height`     | `800`   | viewport height, `200` to `2160`                          |
| `full_page`  | `false` | capture the whole page, cut at 4096 px height             |
| `scale`      | `1`     | device scale factor `1` to `3`, at most 4096 px per side  |
| `wait_until` | `load`  | `load`, `domcontentloaded`, `networkidle`                 |
| `delay`      | `0`     | milliseconds to wait after `wait_until`, up to `10000`    |
| `format`     | `png`   | `png`, `jpeg`, `webp`                                     |

| Case          | Response                                                                                    |
|---------------|---------------------------------------------------------------------------------------------|
| `file`        | the image, filename `<host>.<ext>`                                                          |
| `json`        | `{"data": "<base64>", "content_type": "image/png", "width": 1280, "height": 800, "url": "https://example.com/", "status": 200}` |
| `destination` | `{"destination": "s3://...", "content_type": "image/png", "size": 1234}`                    |

The page and everything it loads follow the `http` settings: private addresses are blocked unless
`http.allow_private` is set, and `http.allowed_hosts` restricts every request the page makes. Pages that answer with
an error status are still captured, `status` reports it.

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
| 413    | `payload_too_large`, `source_too_large`, `image_too_large` | Body or upload too large, remote source exceeds `limits.max_file_size`, image has too many pixels |
| 415    | `unsupported_format`, `unsupported_media_type` | Unsupported document or request body          |
| 422    | `validation_error`, `unsupported_uri`, `unknown_connector`, `page_out_of_range` | Invalid request, URI, connector or page |
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

Download the chunking grammars and tokenizers once into the gitignored `.assets/` (the server never downloads at
runtime, the Docker build does the same):

```shell
uv run python -m scripts.prefetch
```

```shell
uv run fastapi dev --entrypoint server:app --reload
```

Or run development environment with Dekit

```shell
dekit reload --yes
dekit up
```

LibreOffice (legacy formats) only runs in the Linux image. On any OS with Docker, dekit runs the image loop
(`adr/development.md`):

| Task                | What                                                           | Port |
|---------------------|----------------------------------------------------------------|------|
| `docker/dev`        | dev image, source mounted read-only, reload, auth disabled     | 8001 |
| `docker/prod`       | prod image as shipped, API key `dev`                           | 8002 |
| `docker/test`       | full test suite inside the dev image                           |      |

```shell
dekit start docker/dev
dekit start docker/build-dev   # rebuild after changing dependencies or the Dockerfile
```

Format and Lint

```shell
ruff format
ruff check --fix
```

Test, tests marked `full` need the image and are deselected on the host (run them with `dekit start docker/test`)

```shell
uv run pytest
```

## 🐳 Docker

```shell
docker build -t papyrus .
docker run -p 8000:8000 -e PAPYRUS_API_KEY=<key> -v ./config.yml:/etc/papyrus/config.yml:ro papyrus
```

Copy `config.example.yml` to `config.yml` and change what you need; every key is optional and shows its default. Configuration
reference: `adr/architecture.md`.