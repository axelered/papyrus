# Papyrus

## TODO

### Architecture (spec: `adr/architecture.md`)

- [x] Scaffold core: config loader, auth, errors, request parsing, workspace, outputs, health, Dockerfile
- [x] I/O layer: URI parsing, SSRF-safe http fetch, fsspec connectors, destination upload
- [x] Markdown extraction service and `/v1/markdown` (markitdown, spec: `adr/markdown.md`)
- [x] MIME detection with magika, canonical and derived format tables, per-op allowlists, fenced code markdown (spec: `adr/mime.md`)
- [x] Optional structure-aware markdown chunking with chonkie (spec: `adr/chunking.md`)
- [ ] Thumbnail render service and `/v1/thumbnail` for PDF and images (pypdfium2 + Pillow)
- [ ] Virus scan service and `/v1/scan` (clamd inside the image, own ADR)
- [!] Pipeline endpoint with a step DSL over existing services
- [!] Multiple file parts referenced from metadata via `file://<key>`
- [!] Per-service sizing, timeouts and `max_tasks` configurable in YAML
- [!] Per-service queue caps against head-of-line blocking
- [!] Metrics
- [!] External OCR API service
- [!] Embedding-based semantic chunking strategy (chonkie `SemanticChunker`)

### Conversion (spec: `adr/convert.md`)

- [x] LibreOfficeKit conversion engine in a pebble pool, spike gate passed
- [x] Wire `ConvertService` into the service registry and route legacy formats (doc, ppt, rtf, odt, ods, odp) to markdown
- [ ] CI job running the convert tests inside the image
- [!] Public `POST /v1/convert` endpoint (any supported format to pdf or OOXML)
- [!] Office to pdf conversion for thumbnails

## Future Issues

- Starlette parses multipart bodies before size checks; the request body limit must also be enforced at the reverse proxy
- SSRF guard is vulnerable to DNS rebinding until the validated IP is pinned on connect (`server/io/http.py`)
- clamd inside the image needs a multi-process entrypoint and adds about 1 GB RAM per replica
- LibreOffice workers keep network access; deny egress for the worker (network namespace or deployment policy)
- LibreOffice adds about 630 MB to the image and about 330 MB RAM per convert worker, 1 GB+ spikes on large files
