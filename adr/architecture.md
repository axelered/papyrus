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
| Models / config      | Pydantic v2, pydantic-settings with YAML source                |
| Process pools        | `pebble.ProcessPool` (per-task timeout kills the worker)       |
| Remote sources       | httpx (SSRF-guarded)                                           |
| Connectors           | fsspec (s3fs now; other protocols by adding the dependency)    |
| Markdown extraction  | markitdown                                                     |
| Rendering            | pypdfium2 + Pillow (PyMuPDF rejected: AGPL)                    |
| Virus scan           | ClamAV clamd inside the image (own ADR, out of scope here)     |
| Errors               | RFC 9457 `application/problem+json`                            |
| Packaging            | Docker, `python:3.14-slim`, uv multi-stage, non-root           |

## Execution Model

- Synchronous request/response. No job ids, no polling, no shared store.
- **Admission:** a global `asyncio.BoundedSemaphore(workers.max_queue)` caps in-flight requests per container.
  Acquire is non-blocking; when it is exhausted, respond `503 queue_full` with `Retry-After`.
- **Coordinator:** the request coroutine itself. It materializes the source into a workspace, awaits one or more
  services, and builds the output. It holds no CPU work.
- **Deadline:** the whole coordinator runs under `asyncio.timeout(limits.deadline)`; on expiry `504 deadline_exceeded`.
- **Cancellation:** client disconnect or deadline cancels the coordinator; services propagate the cancel to their
  backend (pebble future cancel terminates the running worker).
- todo: one global admission limit allows head-of-line blocking (a markdown flood starves thumbnails within a
  container); add per-service queue caps if metrics show a need.

### Data Plane

- Every request gets a **workspace**: a `TemporaryDirectory` under `limits.tmp_dir`, deleted when the request
  finishes. For file responses, cleanup runs as a response `BackgroundTask` after the body is sent.
- All data moves between components as **file paths** in the workspace, never as in-memory bytes. Original filenames
  (and therefore extensions) are preserved for format detection.
- Backends without filesystem access (remote sidecars, external APIs) receive streamed file content.
- `limits.max_file_size` is enforced on upload copy, http fetch, and connector get (`413 source_too_large`).

## Services

A service is a class with async methods over paths, e.g. `await render.thumbnail(path, opts) -> Path`. It owns its
backend and hides its kind from coordinators. Services compose in coordinators
(e.g. `pdf = await convert.to_pdf(p)` then `await render.thumbnail(pdf, ...)`).

| Kind      | Backend                                 | Capacity control                     | Examples                |
|-----------|-----------------------------------------|--------------------------------------|-------------------------|
| Pool      | `pebble.ProcessPool`, top-level worker fn | `processes`, `timeout`, `max_tasks`  | markdown, render        |
| Daemon    | N supervised long-lived processes       | async queue of instances             | convert (unoserver)     |
| Remote    | TCP / HTTP client                       | `asyncio.Semaphore(concurrency)`     | scan (clamd), OCR API   |

Rules:

- **Lazy start:** a backend (pool, daemons) is created on the first call. Unused services cost nothing.
- **Registry:** services are constructed in the FastAPI lifespan, exposed via a dependency, and closed on shutdown
  (`aclose()` terminates pools and daemons).
- **Heavy imports** (markitdown, pypdfium2, Pillow) happen only inside worker functions so the API process stays light.
- **Pool helper** (`services/pool.py`): wraps `pool.schedule(fn, args, timeout)` with `asyncio.wrap_future`, maps
  `TimeoutError` to `504 processing_timeout`, worker crash to `500 processing_failed`, and cancels the future on
  `CancelledError`.
- **Sizing:** YAML sets only `workers.processes` (default `os.cpu_count()`). Per-service sizes are derived by
  heuristics in code: markdown = render = `processes`, convert = `max(1, processes // 2)` (soffice RAM).
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
| `output`      | `file` or `json`; per-op default (thumbnail=`file`, markdown=`json`, scan=`json` only) |
| `options`     | Op-specific typed model                                                             |

### Transports

A request parser dependency (`api/request.py`) dispatches on `Content-Type` and yields the same model:

- `application/json`: the body is the model. `file://` sources are rejected (`422`).
- `multipart/form-data`: the `metadata` field holds the model as a JSON string; file parts are referenced as
  `file://<part-name>`. A missing referenced part or malformed metadata gives `422 validation_error`.
- Both forms are documented in OpenAPI via `openapi_extra`.
- todo: multiple file parts referenced from metadata by `file://<key>` (the URI scheme already supports it).

### URIs

| Form                            | Source | Destination | Notes                                                |
|---------------------------------|--------|-------------|------------------------------------------------------|
| `file://<part>`                 | yes    | no          | Multipart only                                       |
| `http(s)://...`                 | yes    | no          | SSRF-guarded fetch                                   |
| `<protocol>://<connector>/<path>` | yes  | yes         | Connector must exist and its `protocol` must match the scheme |

Anything else: `422 unsupported_uri`. Unknown connector: `422 unknown_connector`.

### HTTP Fetch Policy

- Resolve the host, reject loopback, private, link-local, ULA and metadata ranges unless `http.allow_private`.
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
| `GET /health`         | no   | Liveness, always 200                                          |
| `GET /ready`          | no   | 503 when admission is exhausted so the LB drains the replica  |
| `POST /v1/markdown`   | yes  | Extract markdown (markitdown)                                 |
| `POST /v1/thumbnail`  | yes  | Render a thumbnail of the first page / image at requested size |
| `POST /v1/scan`       | yes  | Virus scan, JSON result only                                  |

Op-specific options and results are defined in each task's ADR.
todo: generic `POST /v1/pipeline` with a step DSL, built on the same services.

## Auth

- `Authorization: Bearer <key>`; keys come from `PAPYRUS_API_KEY`, comma-separated for zero-downtime rotation.
- Constant-time comparison. Missing or invalid key: `401 unauthorized`.
- Startup fails when `PAPYRUS_API_KEY` is unset, unless `PAPYRUS_AUTH_DISABLED=true` (local development only).

## Configuration

- Path from `PAPYRUS_CONFIG`, default `/etc/papyrus/config.yml`. Missing file means all defaults.
- `${VAR}` placeholders are expanded from the environment before validation; an undefined variable fails startup.
- Validated once at boot (fail fast), immutable afterwards.

```yaml
workers:
  processes: auto          # os.cpu_count()
  max_queue: 64            # in-flight requests per container

limits:
  max_file_size: 100MB
  deadline: 300s
  tmp_dir: /tmp/papyrus

http:
  enabled: true
  allow_private: false
  allowed_hosts: []
  timeout: 30s
  max_redirects: 5

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
`PapyrusError` subclasses carry `status` and `code`; FastAPI validation errors are wrapped into the same shape.

| Status | Codes                                                                              |
|--------|------------------------------------------------------------------------------------|
| 401    | `unauthorized`                                                                     |
| 403    | `source_forbidden`                                                                 |
| 404    | `source_not_found`                                                                 |
| 413    | `source_too_large`                                                                 |
| 415    | `unsupported_format`                                                               |
| 422    | `validation_error`, `unsupported_uri`, `unknown_connector`                         |
| 500    | `processing_failed`                                                                |
| 502    | `source_fetch_failed`, `destination_failed`                                        |
| 503    | `queue_full` (with `Retry-After`), `service_unavailable`                           |
| 504    | `processing_timeout`, `deadline_exceeded`                                          |

## Code Layout

```
server/
  __init__.py          app = create_app()
  app.py               create_app, lifespan, service registry
  config.py            settings models, YAML + ${VAR} loader
  auth.py              bearer dependency
  errors.py            PapyrusError hierarchy, problem+json handlers
  api/
    request.py         JSON | multipart -> model, admission semaphore
    output.py          file | json | destination responses
    health.py          /health, /ready
    markdown.py        route + coordinator
    thumbnail.py       route + coordinator
    scan.py            route + coordinator
  io/
    uri.py             URI parsing and validation
    workspace.py       per-request temp dir
    http.py            SSRF-safe httpx fetch
    connectors.py      fsspec filesystem registry
    transfer.py        materialize(uri) -> Path, deliver(Path, uri)
  services/
    pool.py            lazy pebble pool helper
    markdown.py        MarkdownService + worker fn
    render.py          RenderService + worker fn
    scan.py            ScanService
tests/                 mirrors server/
Dockerfile
```

- Coordinators stay in route modules until composition warrants a `pipelines/` package (arrives with the DSL).

## Deployment

- One container = one uvicorn process. Never use `--workers`: it multiplies pools and splits the admission semaphore.
- Config mounted at `/etc/papyrus/config.yml`; secrets via env.
- Container `HEALTHCHECK` on `/health`; orchestrator readiness on `/ready`.
- Logs: stdlib `logging`, JSON lines, `X-Request-ID` echoed or generated and bound to every line.
- Memory sizing must account for pool processes plus daemons (about 150-300 MB per soffice, about 1 GB for clamd
  signatures).

## Out of Scope

- Virus scan engine integration details (clamd lifecycle, signature updates, multi-process entrypoint): own ADR.
- Conversion service (unoserver daemon pool), printing, OCR: future ADRs, plugging into the service kinds above.
- Metrics, async/webhook delivery, per-service queue caps, pipeline DSL.
