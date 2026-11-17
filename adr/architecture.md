# Papyrus Architecture

Stateless document processing HTTP service. One request processes one document through one or more internal services
and returns the result as a file or JSON, or writes it to a configured destination.

## Goals

- Stateless, horizontally scalable: replicas share nothing; scaling = more containers.
- Bounded resource usage per container: admission limit + bounded process pools, explicit backpressure.
- One uniform request contract across all operations and transports.
- Services isolated behind async interfaces so each backend (process pool, daemon, sidecar, remote API) is
  interchangeable and testable on its own.

## Stack

| Concern              | Choice                                                         |
|----------------------|----------------------------------------------------------------|
| HTTP                 | FastAPI, single uvicorn process per container (no `--workers`) |
| Models / config      | Pydantic v2 models from YAML (pyyaml), pydantic-settings for env |
| Process pools        | `pebble.ProcessPool` (per-task timeout kills the worker)       |
| Remote sources       | httpx (SSRF-guarded)                                           |
| Connectors           | fsspec (s3fs now; other protocols by adding the dependency)    |
| Markdown extraction  | markitdown with `pdf,docx,pptx,xlsx,xls,outlook` extras        |
| Rendering            | pypdfium2 + Pillow (PyMuPDF rejected: AGPL)                    |
| Browser              | Playwright + chrome-headless-shell, see `adr/browser.md`       |
| Virus scan           | ClamAV clamd inside the image (own ADR, out of scope here)     |
| Errors               | RFC 9457 `application/problem+json`                            |
| Packaging            | Docker, `python:3.14-slim`, uv multi-stage, non-root           |

## Execution Model

- Synchronous request/response. No job ids, no polling, no shared store.
- **Admission:** a global non-blocking counter of size `workers.max_queue` caps in-flight requests per container.
  When it is exhausted, respond `503 queue_full` with `Retry-After`.
- **Coordinator:** the request coroutine itself. It materializes the source into a workspace, awaits one or more
  services, and builds the output. It holds no CPU work.
- **Deadline:** the whole coordinator runs under `asyncio.timeout(limits.deadline)`; on expiry `504 deadline_exceeded`.
- **Cancellation:** the deadline cancels the coordinator; services propagate the cancel to their backend (pebble
  future cancel terminates the running worker). todo: uvicorn does not cancel handlers on client disconnect; watch
  `request.is_disconnected()` and cancel the coordinator if abandoned requests waste capacity.
- todo: one global admission limit allows head-of-line blocking (a markdown flood starves thumbnails within a
  container); add per-service queue caps if metrics show a need.

### Data Plane

- Every request gets a **workspace**: a temp directory under `limits.tmp_dir`, deleted when the request finishes. For
  file responses a `FileResponse` subclass deletes it after streaming, also when the client disconnects mid-stream
  (a `BackgroundTask` would be skipped in that case).
- All data moves between components as **file paths** in the workspace, never as in-memory bytes. Original filenames
  are preserved as detection hints.
- After `materialize`, coordinators call `detect` to get a typed `Document` (content-based type, normalized extension)
  and route on it; see `adr/mime.md`.
- Backends without filesystem access (remote sidecars, external APIs) receive streamed file content.
- `limits.max_file_size` is enforced on upload copy (`413 payload_too_large`), http fetch and connector get
  (`413 source_too_large`).

## Services

A service is a class with async methods over paths, e.g. `await render.page(path, target, spec) -> RenderResult`. It owns its
backend and hides its kind from coordinators. Services compose in coordinators
(e.g. `await convert.convert(p, pdf, ConvertFormat.PDF)` then `await render.page(pdf, ...)`).

| Kind      | Backend                                 | Capacity control                     | Examples                |
|-----------|-----------------------------------------|--------------------------------------|-------------------------|
| Pool      | `pebble.ProcessPool`, top-level worker fn | `processes`, `timeout`, `max_tasks`  | markdown, render, convert |
| Daemon    | N supervised long-lived processes       | async queue of instances or semaphore | browser (one Chromium, context per request) |
| Remote    | TCP / HTTP client                       | `asyncio.Semaphore(concurrency)`     | scan (clamd), OCR API   |
| Inline    | In-process, `asyncio.to_thread`         | admission only (ms-scale, GIL-free)  | detect (magika)         |

Rules:

- **Lazy start:** a backend (pool, daemons) is created on the first call. Unused services cost nothing.
- **Registry:** services are constructed in the FastAPI lifespan, exposed via a dependency, and closed on shutdown
  (`aclose()` terminates pools and daemons).
- **Heavy imports** (markitdown, pypdfium2, Pillow) happen only inside worker functions so the API process stays light.
- **Pool helper** (`services/pool.py`): wraps `pool.schedule(fn, args, timeout)` with `asyncio.wrap_future`, maps
  `TimeoutError` to `504 processing_timeout`, worker crash to `500 processing_failed`, and cancels the future on
  `CancelledError`.
- **Worker errors:** worker functions translate library exceptions into `ProblemError` subclasses (raised
  `from None`) so the parent never unpickles library exception types; anything else surfaces as `processing_failed`.
- **Worker arguments and results** are the wire models from `server/types/` (pydantic models pickle), never a parallel
  dataclass mirror.
- **Registry** (`factory.py`): a `Services` dataclass built from `workers` config, closed in a thread on shutdown
  (pool join is blocking).
- **Sizing:** YAML sets only `workers.processes` (default `os.cpu_count()`). Per-service sizes are derived by
  heuristics in code: markdown = render = `processes`, convert = `max(1, processes // 2)` (LibreOffice RAM).
  todo: make per-service sizes, timeouts and `max_tasks` configurable in YAML.
- **Daemon services** own their instance supervision: health checks, restart on crash, restart of the leased instance
  after a timeout before it is returned to the queue.

## Request Contract

One Pydantic request model per operation, sharing common fields:

```json
{
  "source": "s3://prod/my-bucket/docs/a.pdf",
  "destination": "s3://prod/my-bucket/thumbs/a.png",
  "output": "file",
  "options": { "width": 256, "height": 256 }
}
```

| Field         | Rules                                                                              |
|---------------|------------------------------------------------------------------------------------|
| `source`      | Required URI. Defaults to `file://file` in multipart when a `file` part is present |
| `destination` | Optional connector URI. When set, the result is written there and a JSON receipt is returned |
| `output`      | `file` or `json`; per-op default (thumbnail=render=`file`, markdown=`json`, scan=`json` only) |
| `options`     | Op-specific typed model                                                             |

### Transports

`api/request.py` yields the same model for both transports; the generic body parsing (`Content-Type` dispatch,
caps, size-limited upload copy) lives in `utils/body.py`:

- `application/json`: the body is the model. `file://` sources are rejected (`422`).
- `multipart/form-data`: the `metadata` field holds the model as a JSON string; file parts are referenced as
  `file://<part-name>`. A missing referenced part or malformed metadata gives `422 validation_error`.
- Multipart accepts only `metadata` plus file parts (at most 8, unique names); any other form field is rejected so
  options misplaced outside `metadata` never get silently ignored.
- JSON bodies are capped at 1 MiB (`413 payload_too_large`); anything other than JSON or multipart gives
  `415 unsupported_media_type`.
- Model errors use the wrapped validation shape; `loc` is relative to the request model (no `body` prefix).
- Both forms are documented in OpenAPI via `openapi_extra`.
- todo: multiple file parts referenced from metadata by `file://<key>` (the URI scheme already supports it).

### URIs

| Form                            | Source | Destination | Notes                                                |
|---------------------------------|--------|-------------|------------------------------------------------------|
| `file://<part>`                 | yes    | no          | Multipart only                                       |
| `http(s)://...`                 | yes    | no          | SSRF-guarded fetch                                   |
| `<protocol>://<connector>/<path>` | yes  | yes         | Connector must exist and its `protocol` must match the scheme |

Anything else: `422 unsupported_uri`. Unknown connector: `422 unknown_connector`.

- URIs are split literally as `<scheme>://<authority>/<path>` (no `urlsplit`, no percent-decoding) so object keys
  containing `?`, `#` or `%` round-trip.
- Each connector protocol is checked with `fsspec.get_filesystem_class` at startup; a missing implementation fails
  boot. Filesystems are created lazily and cached per connector.
- fsspec calls use the sync API in `asyncio.to_thread`. todo: switch to the native async API of s3fs if thread usage
  becomes a bottleneck.
- `destination` is validated before any processing so a bad target never wastes work.

### HTTP Fetch Policy

- `http.enabled: false` rejects every http(s) source with `403 source_forbidden`.
- Resolve the host and reject it if any address is not public (`ipaddress.is_global`, multicast excluded, IPv4-mapped
  IPv6 unwrapped): loopback, private, CGNAT, link-local/metadata, ULA, unless `http.allow_private`.
- The resolve-and-validate check lives in `utils/net.py`, shared with the browser egress proxy (`adr/browser.md`).
- todo: httpx re-resolves on connect, so DNS rebinding can bypass the check; pin the validated IP in the transport.
- The file name is the decoded URL basename; when it has no extension, one is guessed from `Content-Type`.
- If `http.allowed_hosts` is non-empty, only those hosts are allowed.
- Re-validate on every redirect, at most `http.max_redirects`. Enforce `http.timeout` and `limits.max_file_size`.
- Rejection: `403 source_forbidden`.

### Outputs

- `file`: raw bytes with the correct `Content-Type` and `Content-Disposition` filename.
- `json`: op-specific result model; binary results are inlined as base64 `data` with `content_type`.
- `destination`: upload via the connector, then return the receipt `{destination, content_type, size}`.

## Routes

| Route                 | Auth | Purpose                                                       |
|-----------------------|------|---------------------------------------------------------------|
| `GET /`               | no   | Service name                                                  |
| `GET /health`         | no   | Liveness, always 200                                          |
| `GET /ready`          | no   | 503 when admission is exhausted so the LB drains the replica  |
| `POST /v1/markdown`   | yes  | Extract markdown (markitdown), see `adr/markdown.md`          |
| `POST /v1/thumbnail`  | yes  | Render one page / image into a fixed box, see `adr/render.md`  |
| `POST /v1/render`     | yes  | Render one page / image by width, height or dpi, see `adr/render.md` |
| `POST /v1/screenshot` | yes  | Screenshot a web page in headless Chromium, see `adr/browser.md` |
| `POST /v1/scan`       | yes  | Virus scan, JSON result only                                  |

Op-specific options and results are defined in each task's ADR.
todo: generic `POST /v1/pipeline` with a step DSL, built on the same services.

## Auth

- `Authorization: Bearer <key>`; keys come from `PAPYRUS_API_KEY`, comma-separated for zero-downtime rotation.
- Constant-time comparison. Missing or invalid key: `401 unauthorized`.
- Startup fails when `PAPYRUS_API_KEY` is unset, unless `PAPYRUS_AUTH_DISABLED=true` (local development only).

## Configuration

- Path from `PAPYRUS_CONFIG`, default `/etc/papyrus/config.yml`. Missing file means all defaults.
- `PAPYRUS_*` env vars may also come from a gitignored `.env` in the working directory (local development only; the
  image ships none).
- `${VAR}` placeholders are expanded from the environment in parsed string values (never in raw YAML text, so env
  values cannot inject YAML) before validation; an undefined variable fails startup.
- Unknown keys are rejected. Durations are seconds, sizes accept units (`100MB`, `2MiB`).
- Validated once at boot (fail fast), immutable afterwards.

```yaml
workers:
  processes: auto          # os.cpu_count()
  max_queue: 64            # in-flight requests per container

limits:
  max_file_size: 100MB
  deadline: 300            # seconds
  tmp_dir: /tmp/papyrus    # default: system temp dir + /papyrus

http:
  enabled: true
  allow_private: false
  allowed_hosts: []
  timeout: 30              # seconds
  max_redirects: 5

browser:
  sandbox: true            # see adr/browser.md

connectors:
  prod:
    protocol: s3
    options:               # passed verbatim to fsspec, e.g. s3fs.S3FileSystem(**options)
      endpoint_url: https://s3.eu-central-1.amazonaws.com
      key: ${PROD_S3_KEY}
      secret: ${PROD_S3_SECRET}
```

## Errors

RFC 9457 problem+json with a stable `code` extension. Codes are part of the public contract.

- `utils/problem.py` is generic: `ProblemError` (carries `status`, `code`, `detail`, `headers()`), the generic
  subclasses raised by utils (`ValidationFailed`, `UnsupportedMediaType`, `PayloadTooLarge`), `problem()` and the
  handler installer (problem errors, FastAPI validation, Starlette HTTP errors, unhandled).
- `errors.py` is the application error catalog: every app-specific `ProblemError` subclass, each commented with when
  it is raised. App errors may specialize a generic one (`SourceTooLarge(PayloadTooLarge)`) to refine the `code`.
- FastAPI validation errors are wrapped into the same shape with an `errors` list of `{loc, msg, type}` (inputs are
  never echoed). `ProblemError` takes a single `detail` argument so it pickles across process pools; workers raise
  them directly.

| Status | Codes                                                                              |
|--------|------------------------------------------------------------------------------------|
| 400    | `bad_request` (malformed multipart)                                                |
| 401    | `unauthorized`                                                                     |
| 403    | `source_forbidden`                                                                 |
| 404    | `source_not_found`, `not_found` (unknown route)                                    |
| 405    | `method_not_allowed`                                                               |
| 413    | `payload_too_large` (request body, upload), `source_too_large` (http, connector), `image_too_large` (pixel bomb guard) |
| 415    | `unsupported_format` (document), `unsupported_media_type` (request body)           |
| 422    | `validation_error`, `unsupported_uri`, `unknown_connector`, `page_out_of_range`    |
| 500    | `processing_failed`, `internal_error` (unhandled, details only in logs)            |
| 502    | `source_fetch_failed`, `destination_failed`                                        |
| 503    | `queue_full` (with `Retry-After`), `not_ready` (`/ready`), `service_unavailable`   |
| 504    | `processing_timeout`, `deadline_exceeded`                                          |

## Conventions

- **Owned closed value sets are `StrEnum`** (format labels and groups, output modes, chunk kinds, tokenizers, code
  languages). They validate in Pydantic, appear as OpenAPI enums and serialize as plain strings.
- **Open sets owned by libraries are not mirrored:** internally use the library type (magika `ContentTypeLabel`), at
  the API boundary a documented `str` (`source_type.label`, `source_type.mime_type`), so library upgrades never
  change the API schema.
- **No domain string literals** outside the module that defines the enum; attributes of a value (MIME of a format)
  live on its registry entry, never as separate literals. Allowlists are `frozenset` of enum members.
- API inputs accept closed enums only; error `code`s stay defined once per `PapyrusError` subclass.
- `OpRequest.output` is the `OutputMode` enum (`file`, `json`), defined in `types/common.py`.

## Code Layout

```
scripts/
  prefetch.py          build-time download of grammars and tokenizers (Docker build, local setup)
server/
  __init__.py          app = create_app()
  factory.py           create_app, lifespan, Services registry, config loading (not app.py: would shadow server:app)
  config.py            config and settings schema only
  auth.py              bearer dependency
  errors.py            application error catalog (ProblemError subclasses)
  types/               wire contract: request, result and option models, public enums
    common.py          OpRequest, OutputMode, SourceType, Receipt, FormatGroup, FormatLabel, health responses
    markdown.py        markdown request, options, result, Chunk, Tokenizer, ChunkKind, CodeLanguage
    convert.py         ConvertFormat
    render.py          thumbnail and render requests, options, RenderResult, ImageFormat, Fit
    screenshot.py      screenshot request, options, result, WaitUntil
  utils/               generic primitives
    problem.py         ProblemError, generic errors, problem+json rendering and handlers
    body.py            JSON | multipart body reading, caps, size-limited upload copy
    openapi.py         schema helpers ($ref inlining)
    yaml.py            YAML loading with ${VAR} expansion
    logs.py            JSON log formatter, X-Request-ID middleware
    text.py            text decoding, CommonMark fences
    net.py             host resolution and public address policy
    proxy.py           pinning forward proxy (CONNECT and http)
  api/
    admission.py       non-blocking in-flight counter (event loop only, no semaphore needed)
    request.py         op context (admission, workspace, deadline), body -> model, OpenAPI request body
    output.py          file | json | destination responses
    health.py          /, /health, /ready
    markdown.py        route + coordinator
    render.py          /v1/thumbnail and /v1/render routes + shared coordinator
    screenshot.py      route + coordinator
    scan.py            route + coordinator
  io/
    uri.py             URI parsing and validation
    workspace.py       per-request temp dir
    http.py            SSRF-safe httpx fetch
    connectors.py      fsspec filesystem registry
    transfer.py        materialize(uri) -> Path, deliver(Path, uri)
  services/
    pool.py            lazy pebble pool helper
    detect.py          DetectService (magika, inline), format table and resolution
    chunking.py        chunker, asset configuration, code language resolution
    markdown.py        MarkdownService + worker fns
    convert.py         ConvertService + worker fn (LibreOfficeKit)
    lok.py             LibreOfficeKit ctypes binding
    render.py          RenderService + worker fn (pypdfium2, Pillow)
    browser.py         BrowserService (Playwright), egress policy wiring
    scan.py            ScanService
test/                  mirrors server/, shared fixtures in test/fixtures/
Dockerfile
```

### Layering

Imports only point down; a violation is a design bug, not a style nit.

| Layer                    | May import                                   |
|--------------------------|----------------------------------------------|
| `types/`                 | stdlib, pydantic                             |
| `utils/`                 | stdlib, third-party; never `server.*`        |
| `errors.py`, `config.py` | `utils/`                                     |
| `io/`, `services/`       | `types/`, `utils/`, `errors.py`, `config.py` |
| `api/`, `auth.py`        | everything above                             |
| `factory.py`             | everything (wiring)                          |

- `types/` holds only what crosses the wire, so a client SDK can be generated from (or extracted with) it; one module
  per operation, shared models in `common.py`. Internal models (`Format`, `Document`) live with their service.
- `utils/` holds primitives that would work unchanged in another service. Errors they raise are generic
  `ProblemError` subclasses; utils needing a logger take it as a parameter.
- A module that mixes a generic primitive with app logic is split: the primitive goes to `utils/`, the app part stays.
  A helper with one consumer lives in that consumer, not in its own file.
- Build-time and operational scripts live in `scripts/`, never inside the `server` package.
- Coordinators stay in route modules until composition warrants a `pipelines/` package (arrives with the DSL).

## Deployment

- One container = one uvicorn process under `tini` (reaps orphaned Chromium processes). Never use `--workers`: it
  multiplies pools and splits the admission semaphore.
- Image: multi-stage build (`uv sync --locked --no-dev` into `/app/.venv`, then a clean `python:3.14-slim` base
  with LibreOffice; a `dev` stage for local and CI testing, `adr/development.md`), runs as
  non-root uid 10001, 30s graceful shutdown. Only runtime dependencies are installed, so anything the server needs
  (uvicorn, python-multipart) must be a main dependency, not only part of `fastapi[standard]`.
- Config mounted at `/etc/papyrus/config.yml`; secrets via env.
- No runtime downloads: anything a library would fetch lazily (tree-sitter grammars, tiktoken encodings) is
  prefetched at Docker build into fixed image paths (`adr/chunking.md`).
- Container `HEALTHCHECK` on `/health`; orchestrator readiness on `/ready`.
- Logs: stdlib `logging` under the `papyrus.*` logger namespace, JSON lines on stderr, `X-Request-ID` echoed (if it
  matches `[A-Za-z0-9._:-]{1,128}`) or generated and bound to every line. Uvicorn keeps its own access log format.
- Memory sizing must account for pool processes plus daemons (about 330 MB per convert worker, about 1 GB for
  clamd signatures, about 150 MB plus 150 MB per context for the browser).

## Out of Scope

- Virus scan engine integration details (clamd lifecycle, signature updates, multi-process entrypoint): own ADR.
- Printing, OCR: future ADRs, plugging into the service kinds above. Conversion: `adr/convert.md`, rendering:
  `adr/render.md`, browser: `adr/browser.md`.
- Metrics, async/webhook delivery, per-service queue caps, pipeline DSL.
