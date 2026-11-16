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
| Markdown extraction  | markitdown                                                     |
| Rendering            | pypdfium2 + Pillow (PyMuPDF rejected: AGPL)                    |
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
- Multipart accepts only `metadata` plus file parts (at most 8, unique names); any other form field is rejected so
  options misplaced outside `metadata` never get silently ignored.
- JSON bodies are capped at 1 MiB; anything other than JSON or multipart gives `415 unsupported_media_type`.
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
`PapyrusError` subclasses carry `status` and `code`; FastAPI validation errors are wrapped into the same shape with an
`errors` list of `{loc, msg, type}` (inputs are never echoed). `PapyrusError` takes a single `detail` argument so it
pickles across process pools; workers raise them directly.

| Status | Codes                                                                              |
|--------|------------------------------------------------------------------------------------|
| 400    | `bad_request` (malformed multipart)                                                |
| 401    | `unauthorized`                                                                     |
| 403    | `source_forbidden`                                                                 |
| 404    | `source_not_found`, `not_found` (unknown route)                                    |
| 405    | `method_not_allowed`                                                               |
| 413    | `source_too_large`                                                                 |
| 415    | `unsupported_format` (document), `unsupported_media_type` (request body)           |
| 422    | `validation_error`, `unsupported_uri`, `unknown_connector`                         |
| 500    | `processing_failed`, `internal_error` (unhandled, details only in logs)            |
| 502    | `source_fetch_failed`, `destination_failed`                                        |
| 503    | `queue_full` (with `Retry-After`), `not_ready` (`/ready`), `service_unavailable`   |
| 504    | `processing_timeout`, `deadline_exceeded`                                          |

## Code Layout

```
server/
  __init__.py          app = create_app()
  factory.py           create_app, lifespan, service registry (not app.py: would shadow the server:app attribute)
  logs.py              JSON log formatter, X-Request-ID middleware
  config.py            settings models, YAML + ${VAR} loader
  auth.py              bearer dependency
  errors.py            PapyrusError hierarchy, problem+json handlers
  api/
    admission.py       non-blocking in-flight counter (event loop only, no semaphore needed)
    request.py         JSON | multipart -> model, op context (admission, workspace, deadline)
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
test/                  mirrors server/, shared fixtures in test/fixtures/
Dockerfile
```

- Coordinators stay in route modules until composition warrants a `pipelines/` package (arrives with the DSL).

## Deployment

- One container = one uvicorn process. Never use `--workers`: it multiplies pools and splits the admission semaphore.
- Config mounted at `/etc/papyrus/config.yml`; secrets via env.
- Container `HEALTHCHECK` on `/health`; orchestrator readiness on `/ready`.
- Logs: stdlib `logging` under the `papyrus.*` logger namespace, JSON lines on stderr, `X-Request-ID` echoed (if it
  matches `[A-Za-z0-9._:-]{1,128}`) or generated and bound to every line. Uvicorn keeps its own access log format.
- Memory sizing must account for pool processes plus daemons (about 150-300 MB per soffice, about 1 GB for clamd
  signatures).

## Out of Scope

- Virus scan engine integration details (clamd lifecycle, signature updates, multi-process entrypoint): own ADR.
- Conversion service (unoserver daemon pool), printing, OCR: future ADRs, plugging into the service kinds above.
- Metrics, async/webhook delivery, per-service queue caps, pipeline DSL.
