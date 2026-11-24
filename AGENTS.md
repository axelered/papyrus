# Papyrus

## Repository Rules

- This is a product developed by Axelered
- Changes to the configuration should always be reflected in `config.example.yml` where shall be kept up-to-date and correctly documented for a human reader that will use such file as a template to configure their deployment
- New APIs and updates to existing APIs shall be reflected in the development documentation via Bruno present in the `./bruno` folder. Always introduce files for new APIs and provide an exhaustive set of examples with proper human-readable descriptions. When relevant use our website `https://axelered.com` in the examples

## TODO

### Architecture (spec: `adr/architecture.md`)

- [x] Scaffold core: config loader, auth, errors, request parsing, workspace, outputs, health, Dockerfile
- [x] I/O layer: URI parsing, SSRF-safe http fetch, fsspec connectors, destination upload
- [x] Markdown extraction service and `/v1/markdown` (markitdown, spec: `adr/markdown.md`)
- [x] MIME detection with magika, canonical and derived format tables, per-op allowlists, fenced code markdown (spec: `adr/mime.md`)
- [x] Optional structure-aware markdown chunking with chonkie (spec: `adr/chunking.md`)
- [!] Pipeline endpoint with a step DSL over existing services
- [!] Multiple file parts referenced from metadata via `file://<key>`
- [!] Per-service sizing, timeouts and `max_tasks` configurable in YAML
- [!] Per-service queue caps against head-of-line blocking
- [!] Metrics
- [!] Embedding-based semantic chunking strategy (chonkie `SemanticChunker`)

### Inspect (spec: `adr/inspect.md`)

- [x] `POST /v1/inspect`: materialize and detect, return standardized source info and the type mismatch, no worker
- [ ] Structural probe: pdfium (page count, encrypted) and Pillow `verify` (size, truncation) in the render pool, zip central directory and member and ratio checks, strict UTF-8 and JSON parse; validity verdict, none for legacy office and `msg`

### Development and CI (spec: `adr/development.md`)

- [x] `dev` Docker stage, dekit tasks for dev/prod containers and tests, `full` test mark
- [x] GitHub Actions check job building the image and running the full suite inside it
- [x] Publish the image to GHCR on `v*` tags
- [x] Versioning: semver stamps `server/version.py`, watermark build arg, both returned by `GET /`
- [x] GitHub release with the semver changelog after the image push
- [ ] Review and simplify the whole CI/CD pipeline and Dockerfile: publish gating, job split (check vs release), summary step, stage layout

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
- [x] HTML thumbnails as browser screenshots: routed document origin, scheme guard, security spike tests
- [!] HTML in `/v1/render` through Chromium (HTML > PDF > image), uploaded HTML for `/v1/screenshot`
- [!] Pin the validated IP in the httpx transport via `utils/net.py`

### OCR (spec: `adr/ocr.md`)

- [x] Adapters in `server/ocr/` (qwen, mistral) with prompts, cleaning and error types, `live` test mark
- [ ] Validate `mistral` against the real API: tables, truncation, limits
- [!] Quality and speed on real documents, hidden slides in the PDF export
- [x] `ocr` config section, `OcrService` with slots, render cap and retries, new error codes
- [ ] `RenderSpec.max_pixels`, `options.readMode` in `/v1/markdown`: formats, fallback, page limit, page markers
- [ ] `chunks[].page` from page markers
- [ ] `config.example.yml`, Bruno, docs, README, chart `extraEgress` note
- [!] `readMode: auto`: OCR only for pages without a usable text layer
- [!] `options.pages` to split documents over `ocr.max_pages`
- [!] `options.onPageError: skip`
- [!] Whole document in one call for Mistral
- [!] Image descriptions, option to drop page headers and footers
- [!] Token and cost reporting

### Request Identity (spec: `adr/architecture.md`, `adr/browser.md`)

- [x] `User-Agent: Papyrus/<version>` in `HttpFetcher`
- [x] `http.locale` (default `en-US`): `Accept-Language` in the fetcher, `locale` in the browser context, `config.example.yml` and docs
- [!] Per-request `options.locale` overriding `http.locale`

### Performance (spec: `adr/performance.md`)

- [x] `Server-Timing` stages behind `PAPYRUS_TIMING` and the `scripts/bench.py` cold/hot pool benchmark
- [x] Pool workers import their module without building the app or importing FastAPI
- [x] Warm services at startup behind `workers.warmup`: `Service` base, `utils/pool.py` with initializer, `AsyncExitStack` lifespan
- [ ] Website thumbnails: drop the httpx pre-fetch and route only the document navigation through Python

### API Contract (spec: `adr/api.md`)

- [x] camelCase `ApiModel` base, `{source, options, result}` envelope with shared `FileInfo`, resolved options echo, light `destination` responses
- [x] Op file rule: JSON document as file and destination output when data sections are present (chunking)
- [x] OpenAPI: named components, `Problem` with generated `code` enum, per-route errors, multipart encoding, spec validator test, `scripts/openapi.py`
- [x] Bruno docs to camelCase and the new shapes
- [ ] Tag `v0.3.0` and announce the breaking change in the release notes
- [!] Metadata for `output=file` responses, opt-in data URI content
- [ ] `sha256` in `FileInfo`, filled for `source` and `result` by every operation

### Python SDK (spec: `adr/sdk-python.md`)

- [x] Spec: handwritten `papyrus-sdk` in `sdk/python/`, sync and async clients
- [x] `sdk/python/` package: typed models and enums, errors, `Papyrus` and `AsyncPapyrus`, retries, `health` and `ready`
- [x] `sdk/python/test/`: schema, parity, integration and client tests; mounts in dekit and `check.yml`
- [x] `sdk/python/README.md`, `LICENSE` and `NOTICE`
- [!] Publish to PyPI before the next tag, the guide is already in `docs/`: version stamp, wheel check on 3.11, Trusted Publishing
- [x] `docs/python.mdx` guide
- [!] Add `hatchling` to the SBOM of `adr/license.md` once `feat/license` is merged
- [x] Move the project to `sdk/python/` and its tests from `test/sdk/` to `sdk/python/test/`

### TypeScript SDK (spec: `adr/sdk-typescript.md`)

- [x] Spec: handwritten `@axelered/papyrus` in `sdk/typescript/`, on web standard APIs
- [x] `sdk/typescript/` package: schemas and types, errors, `Papyrus`, retries, `health` and `ready`
- [x] Schema, client, model, integration and package tests on Node and Bun, dekit task
- [x] `sdk/typescript/README.md`, `LICENSE` and `NOTICE`
- [x] `docs/typescript.mdx` guide
- [!] Publish to npm before the next tag, the guide is already in `docs/`: `axelered` organization, version stamp, Trusted Publishing
- [!] Run the TypeScript tests in `check.yml`, until then drift is caught only locally
- [!] Expose `Retry-After` to browsers in `server/utils/cors.py`
- [!] Add `zod` and the development tools to `adr/license.md` once `feat/license` is merged

### Helm Chart (spec: `adr/chart.md`)

- [x] Chart in `chart/`: ConfigMap, Secret with generated key, Deployment, Service, Ingress, PDB, opt-in NetworkPolicy
- [ ] Move stage to `chart/values.stage.nogit.yml` and delete `chart/deploy.nogit.yml`
- [!] `helm lint` and `helm template` in the check job
- [!] Package and push the chart to `oci://ghcr.io/axelered/charts` on release
- [!] `docs/kubernetes.mdx` guide

### Documentation (spec: `adr/docs.md`)

- [x] Docs spec: readers, language, structure; `/document` command and `axelered-docs` skill
- [x] Guides: introduction, Docker quickstart, guides, concepts and deploy pages
- [x] Bruno: collection overview, endpoint docs, examples and `seq` order for every public endpoint
- [x] Complete `Field(description=…)` and route summaries in FastAPI for the API reference
- [x] Docs bundle in the check workflow, attached to the GitHub release
- [ ] Onboarding on `axelered/docs`: GitHub App, secrets, `products.yml` entry, release dispatch
- [!] Slim the README once the docs site covers its content

## Future Issues

- Starlette parses multipart bodies before size checks; the request body limit must also be enforced at the reverse proxy
- SSRF guard is vulnerable to DNS rebinding until the validated IP is pinned on connect (`server/io/http.py`)
- Chromium runs without its sandbox by default (Docker seccomp blocks user namespaces): a renderer exploit runs as the API user with access to secrets and workspaces
- Pool workers have no memory limit: an image inside a hostile PDF can expand to gigabytes in pdfium, outside Pillow's bomb guard, and the OOM kill can take the API process
- LibreOffice workers keep network access; deny egress for the worker (network namespace or deployment policy)
- LibreOffice adds about 630 MB to the image and about 330 MB RAM per convert worker, 1 GB+ spikes on large files
- `workers.processes: auto` uses `os.cpu_count()`, which ignores container CPU limits: a 2 CPU container on a 32 core host starts 32 processes per pool
- The docs site drops Bruno error examples: `json` bodies map to `application/json`, errors are `application/problem+json`, and statuses covered only by the `4XX` and `5XX` ranges are skipped as not in the contract (axelered/docs `scripts/bruno.ts`)
- Chunking: a heading directly followed by a table becomes a heading-only chunk, and `overlap` skips the table to the next prose chunk
