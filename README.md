# 📄 Papyrus

Axelered Papyrus is a self-hosted HTTP API that converts documents and web pages into Markdown, chunks and images for LLM and agent pipelines.

## 📚 Supported Files

| Format     | Extensions             | Notes                                   |
|------------|------------------------|-----------------------------------------|
| PDF        | `.pdf`                 | Text layer, or OCR for scans            |
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
| Images     | `.png`, `.jpg`, `.webp`, `.gif`, `.bmp`, `.tiff`, `.avif` | Thumbnail, render, Markdown with OCR |

The format is detected from the content (magika); the extension only refines generic matches such as `.csv` or
`.rss`. Mislabeled files are processed by their real type and reported in `source`.

## 🔌 API

Full documentation: [docs.axelered.com/papyrus](https://docs.axelered.com/papyrus). The Bruno collection in `bruno/` has a request and real examples for every endpoint.

| Endpoint              | Does                                                              | Default output |
|-----------------------|-------------------------------------------------------------------|----------------|
| `POST /v1/markdown`   | Markdown from a document or web page, with optional OCR and RAG chunks | `json`         |
| `POST /v1/render`     | One page or image frame at a readable size                        | `file`         |
| `POST /v1/thumbnail`  | One page, image or web page fitted into a box                     | `file`         |
| `POST /v1/screenshot` | A web page captured in headless Chromium                          | `file`         |
| `POST /v1/inspect`    | Detected format and checks of a document, without processing it   | `json`         |
| `GET /`, `/health`, `/ready` | Version, liveness and readiness, public                    | `json`         |

All `/v1` routes require `Authorization: Bearer <key>`. Requests are `application/json`, or `multipart/form-data` with the upload in `file` and the request as JSON in `metadata`. Keys are camelCase.

| Field         | Description                                                                     |
|---------------|---------------------------------------------------------------------------------|
| `source`      | `file://<part>`, `http(s)://...` or `<protocol>://<connector>/<path>`; defaults to `file://file` on uploads |
| `destination` | Optional connector URI, the result is written there and the response omits `content` |
| `output`      | `json` for the envelope, `file` for the artifact as the body                    |
| `options`     | Operation options, echoed back with defaults applied                            |

Inspect takes only `source` and `options`: it rejects `destination` and `output`, and its response has no `result`.

```shell
curl -H "Authorization: Bearer <key>" -F file=@report.docx http://localhost:8000/v1/markdown
curl -H "Authorization: Bearer <key>" -H 'content-type: application/json' \
  -d '{"source": "s3://prod/docs/report.pdf", "destination": "s3://prod/md/report.md"}' \
  http://localhost:8000/v1/markdown
```

`"options": {"readMode": "ocr"}` reads scans and images with a Qwen VL or Mistral OCR backend, off until you add `ocr` to the configuration.

The other operations answer JSON with the `{source, result, options}` envelope, plus data sections such as `chunks`:

```json
{
  "source": {"uri": "file://file", "name": "report.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "extension": "docx", "label": "docx", "group": "document"},
  "result": {"name": "report.md", "mimeType": "text/markdown", "extension": "md", "label": "markdown", "group": "markup", "encoding": "utf-8", "content": "# Report ..."},
  "options": {"readMode": "text"}
}
```

Errors are `application/problem+json` with a stable `code`, listed in the [errors page](https://docs.axelered.com/papyrus/errors). Interactive docs are served at `/docs`.

## 🧑🏼‍💻 Development

The server refuses to start without an API key. For local development create a gitignored `.env`:

```shell
PAPYRUS_AUTH_DISABLED=true
PAPYRUS_CONFIG=config.local.yml
```

Logs are plain text on stderr. `PAPYRUS_LOG_LEVEL` (`debug`, `info`, `warning`, `error`) sets the papyrus
loggers, uvicorn keeps its own `--log-level`, and `PAPYRUS_LOG_FORMAT=json` switches every line to JSON for log
pipelines.

To profile requests set `PAPYRUS_TIMING=true`: every response then carries a `Server-Timing` header with the
duration of each stage (`parse`, `materialize`, `detect`, `convert`, `render`, `browser.goto`, ...) and the same
values are logged once per request. Keep it off in production, the header reveals internal timings.
`docker exec papyrus-dev python -m scripts.bench` prints cold and hot latency per service operation
(`adr/performance.md`).

Download the chunking grammars and tokenizers once into the gitignored `.assets/` (the server never downloads at
runtime, the Docker build does the same):

```shell
uv run python -m scripts.prefetch
```

```shell
uv run fastapi dev --entrypoint server.main:app --reload
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

## 🤖 Agents

This repository s configured to use `work3` to manage high performance agentic development. Install via `bun install -g @svara/work3`.

## 🐳 Docker

```shell
docker build -t papyrus .
docker run -p 8000:8000 -e PAPYRUS_API_KEY=<key> -v ./config.yml:/etc/papyrus/config.yml:ro papyrus
```

Copy `config.example.yml` to `config.yml` and change what you need; every key is optional and shows its default. Configuration
reference: [docs.axelered.com/papyrus/configuration](https://docs.axelered.com/papyrus/configuration).

## ⚖️ License

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright Axelered D.o.o. and Axelered LLC-Fz.
