# Papyrus

## Local Rules

- This is a product developed by Axelered, use our website `https://axelered.com` in the examples

## TODO

### Architecture (spec: `adr/architecture.md`)

- [x] Scaffold core: config loader, auth, errors, request parsing, workspace, outputs, health, Dockerfile
- [x] I/O layer: URI parsing, SSRF-safe http fetch, fsspec connectors, destination upload
- [x] Markdown extraction service and `/v1/markdown` (markitdown, spec: `adr/markdown.md`)
- [x] MIME detection with magika, canonical and derived format tables, per-op allowlists, fenced code markdown (spec: `adr/mime.md`)
- [x] Optional structure-aware markdown chunking with chonkie (spec: `adr/chunking.md`)
- [ ] Virus scan service and `/v1/scan` (clamd inside the image, own ADR)
- [!] Pipeline endpoint with a step DSL over existing services
- [!] Multiple file parts referenced from metadata via `file://<key>`
- [!] Per-service sizing, timeouts and `max_tasks` configurable in YAML
- [!] Per-service queue caps against head-of-line blocking
- [!] Metrics
- [!] External OCR API service
- [!] Embedding-based semantic chunking strategy (chonkie `SemanticChunker`)

### Development and CI (spec: `adr/development.md`)

- [x] `dev` Docker stage, dekit tasks for dev/prod containers and tests, `full` test mark
- [x] GitHub Actions check job building the image and running the full suite inside it
- [x] Publish the image to GHCR on `v*` tags
- [x] Versioning: semver stamps `server/version.py`, watermark build arg, both returned by `GET /`
- [x] GitHub release with the semver changelog after the image push

### Conversion (spec: `adr/convert.md`)

- [x] LibreOfficeKit conversion engine in a pebble pool, spike gate passed
- [x] Wire `ConvertService` into the service registry and route legacy formats (doc, ppt, rtf, odt, ods, odp) to markdown
- [!] Public `POST /v1/convert` endpoint (any supported format to pdf or OOXML)

### Rendering (spec: `adr/render.md`)

- [x] Image labels and `image` group in the format table, `image_too_large` and `page_out_of_range` errors
- [x] `RenderService` (pypdfium2 + Pillow) with `/v1/thumbnail` and `/v1/render`
- [x] Office and HTML to single-page pdf via `PageRange`, spike for out of range and HTML pagination
- [!] Multi-page output to a destination prefix
- [!] `heic`/`avif` and `svg` input, `quality` option

### Browser (spec: `adr/browser.md`)

- [x] Extract `utils/net.py` from `io/http.py` and add the `utils/proxy.py` pinning proxy
- [x] `tini` entrypoint, Playwright headless shell and fonts in the image
- [x] Spike gate: sandbox as non-root, egress coverage, rebinding, crash and recycle behaviour
- [ ] Security: enable the Chromium sandbox by default, ship a seccomp profile allowing user namespaces
- [x] `BrowserService` and `POST /v1/screenshot`
- [ ] HTML thumbnails as browser screenshots: routed document origin, scheme guard, security spike tests
- [!] HTML in `/v1/render` through Chromium (HTML > PDF > image), uploaded HTML for `/v1/screenshot`
- [!] Pin the validated IP in the httpx transport via `utils/net.py`

## Future Issues

- Starlette parses multipart bodies before size checks; the request body limit must also be enforced at the reverse proxy
- SSRF guard is vulnerable to DNS rebinding until the validated IP is pinned on connect (`server/io/http.py`)
- Chromium runs without its sandbox by default (Docker seccomp blocks user namespaces): a renderer exploit runs as the API user with access to secrets and workspaces
- clamd inside the image needs a multi-process entrypoint and adds about 1 GB RAM per replica
- LibreOffice workers keep network access; deny egress for the worker (network namespace or deployment policy)
- LibreOffice adds about 630 MB to the image and about 330 MB RAM per convert worker, 1 GB+ spikes on large files
