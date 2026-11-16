# Papyrus

## TODO

### Architecture (spec: `adr/architecture.md`)

- [x] Scaffold core: config loader, auth, errors, request parsing, workspace, outputs, health, Dockerfile
- [x] I/O layer: URI parsing, SSRF-safe http fetch, fsspec connectors, destination upload
- [x] Markdown extraction service and `/v1/markdown` (markitdown, spec: `adr/markdown.md`)
- [ ] MIME detection with magika, canonical and derived format tables, per-op allowlists, fenced code markdown (spec: `adr/mime.md`)
- [ ] Thumbnail render service and `/v1/thumbnail` for PDF and images (pypdfium2 + Pillow)
- [ ] Virus scan service and `/v1/scan` (clamd inside the image, own ADR)
- [!] Pipeline endpoint with a step DSL over existing services
- [!] Convert service with a supervised unoserver daemon pool (office thumbnails, conversion)
- [!] Multiple file parts referenced from metadata via `file://<key>`
- [!] Per-service sizing, timeouts and `max_tasks` configurable in YAML
- [!] Per-service queue caps against head-of-line blocking
- [!] Metrics
- [!] External OCR API service
- [!] Legacy format routes (doc, ppt, rtf, odt, ods, odp) through the convert service
- [!] Context-aware markdown chunking based on the detected source type

## Future Issues

- Starlette parses multipart bodies before size checks; the request body limit must also be enforced at the reverse proxy
- SSRF guard is vulnerable to DNS rebinding until the validated IP is pinned on connect (`server/io/http.py`)
- clamd inside the image needs a multi-process entrypoint and adds about 1 GB RAM per replica
