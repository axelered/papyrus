# Browser Engine

Headless Chromium for content that needs a real browser: `POST /v1/screenshot` of live web pages now, full-fidelity
HTML rendering later. Extends the service kinds of `adr/architecture.md`; image output reuses `ImageFormat` and the
pixel cap of `adr/render.md`.

## Goals

- Untrusted pages, including JavaScript, can never reach a destination the `http.*` policy denies: every
  connection is resolved, checked and pinned by Papyrus, closing the DNS rebinding gap for the browser.
- One long-lived browser per container, isolated contexts per request, no process spawned per request.
- A hostile page fails its own request (renderer crash, timeout) and never takes down the container.
- No TCP debug port, no runtime downloads, lazy start: deployments that never screenshot pay nothing.
- todo: functional integration of HTML into `/v1/render` and `/v1/thumbnail` (e.g. `pdf()` print to PDF, then
  pdfium) and uploaded `file://` HTML for screenshots; refined in a later iteration.

## Decision

Async Playwright driven from the API process, one supervised browser, one `BrowserContext` per request.

| Option                                  | Verdict                                                                     |
|-----------------------------------------|-----------------------------------------------------------------------------|
| Async Playwright in the API process     | **Chosen**: Chromium does the work out of process, the event loop only awaits; semaphore capacity like the Remote kind |
| Pebble pool, sync Playwright per worker | Rejected: killing a worker on timeout can orphan the driver and Chromium, N browsers cost N x 150 MB+ |
| Raw CDP to Debian `chromium`            | Rejected: reimplements Playwright                                           |
| Sidecar (browserless, Gotenberg)        | Rejected: second container and deployment unit (same as `adr/convert.md`)   |
| LibreOffice HTML import                 | Kept for HTML documents in render routes until the functional integration   |

The model mirrors Gotenberg (one supervised Chromium, context per conversion, restart after N conversions, internal
DNS pinning proxy), which validates the egress design below.

## Stack

| Concern  | Choice                                                                                       |
|----------|----------------------------------------------------------------------------------------------|
| Driver   | `playwright` (main dependency, wheel bundles the Node driver)                                |
| Browser  | `chrome-headless-shell` via `playwright install --with-deps --only-shell chromium` at Docker build into `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright` |
| Fonts    | `fonts-liberation` (already present), `fonts-noto-color-emoji`                               |
| Init     | `tini` as image entrypoint (see Process Model)                                               |
| Egress   | `utils/proxy.py` pinning forward proxy in the API event loop                                 |

## Process Model

```
tini (PID 1)
└─ uvicorn: event loop, routes, PinningProxy coroutine on 127.0.0.1:<random>
   ├─ pebble pools (unchanged)
   └─ node: Playwright driver, stdin/stdout pipe to Python, spawned on first screenshot
      └─ chrome-headless-shell browser, CDP over --remote-debugging-pipe (no TCP port)
         ├─ zygote, network service, storage service, GPU process (software)
         └─ renderers: one per site per context, created and destroyed with pages
```

- `tini` is required: Chromium churns short-lived processes and orphaned descendants are reparented to PID 1;
  uvicorn never reaps them. `ENTRYPOINT ["tini", "--"]` also forwards signals. It also serves the future clamd
  multi-process entrypoint.
- If Python dies hard the chain ends by pipe EOF (the driver exits on stdin close, Chromium on CDP pipe close).

### Lifecycle

| Event                    | Handling                                                                        | Result |
|--------------------------|---------------------------------------------------------------------------------|--------|
| First call               | `async_playwright().start()` then `chromium.launch(args, proxy)` under an `asyncio.Lock` | One launch even under concurrent first calls |
| Request                  | `Semaphore(concurrency)` -> `new_context` -> `goto` -> optional delay -> `screenshot` -> `context.close()` in `finally` | Renderers exit with the context |
| Timeout, deadline cancel | Operation under `asyncio.timeout(30)`, then `context.close()` (kills renderers stuck in JS loops) | `504`, browser stays healthy |
| Renderer crash           | Page `crash` event                                                              | `500` for that request only |
| Browser or driver crash  | `browser.on('disconnected')` marks it dead                                      | In-flight `500`, next call relaunches lazily (no restart loop) |
| Recycle after 200 contexts | Launch a fresh browser for new contexts, close the old one when its in-flight count reaches 0 | No killed requests |
| Shutdown                 | `aclose()`: `browser.close()`, `playwright.stop()`, proxy close                 | Within the 30s grace period |

### Sizing

- Concurrency: `max(1, workers.processes // 2)` contexts (convert heuristic, each tab costs 50-150 MB).
- `--js-flags=--max-old-space-size=512` caps the V8 heap per renderer: a memory bomb crashes its renderer (`500`)
  instead of OOM-killing the container with the pebble pools.
- Full-page captures are capped at 4096 px height (`adr/render.md`), so infinite scroll cannot exhaust memory.
- Memory: about 150 MB idle browser plus about 150 MB per concurrent context; image grows by about 300 MB.

### Sandbox

- Launch with `chromium_sandbox=True` (Playwright defaults to `--no-sandbox`): untrusted JS must not run unsandboxed
  next to `PAPYRUS_API_KEY` and connector secrets.
- The sandbox needs unprivileged user namespaces; when the runtime blocks them launch fails loudly
  (`500 processing_failed`).
- Opt-out `browser.sandbox: false` is explicit and logs a warning at startup; never a silent fallback.

## Egress

Every TCP connection of the browser goes through the pinning proxy; UDP paths are disabled.

- `server/utils/proxy.py`: generic `PinningProxy(check)` asyncio forward proxy, `start() -> port`, `aclose()`.
  Handles `CONNECT host:port` tunnels (https, wss; TLS stays end to end, never decrypted) and absolute-URI
  `http://` forwarding. `check(host, port) -> IPAddress` raises on deny; the proxy connects to the returned IP
  (the pin) and answers `403` on denial.
- `server/utils/net.py`: `resolve_allowed(host, allowed_hosts, allow_private) -> IPAddress`, moved out of
  `io/http.py` (which now uses it, behaviour unchanged). Resolves once; any non-global address denies.
- `services/browser.py` wires the `http.*` policy as `check`, gives each context proxy credentials (a per-request
  token) and records denied hosts per token, so a denied top-level navigation becomes `403 source_forbidden` while a
  denied subresource just fails inside the page.
- Chromium flags: `--proxy-server=127.0.0.1:<port>`, `--proxy-bypass-list=<-loopback>` (otherwise loopback bypasses
  the proxy), `--disable-quic`, `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`.
- Context options: `service_workers='block'`; navigation to `file:`, `chrome:` and other non-http schemes is denied.
- Defense in depth (documented, not enforced): deny egress to private ranges at the deployment level.
- todo: pin the validated IP in the httpx transport via `utils/net.py` to close the rebinding gap of `io/http.py`.

## Route

`POST /v1/screenshot`, `server/api/screenshot.py`, default `output` is `file`.

- `source` is `http(s)://` only; it is navigated, never materialized. `http.enabled: false` gives
  `403 source_forbidden`, as for fetch. Other schemes give `422 unsupported_uri`.

| Option       | Default | Rules                                                       |
|--------------|---------|-------------------------------------------------------------|
| `width`      | `1280`  | Viewport, `320..3840`                                       |
| `height`     | `800`   | Viewport, `200..2160`                                       |
| `full_page`  | `false` | Capture height capped at 4096 px                            |
| `scale`      | `1`     | `device_scale_factor`, `1..3`                               |
| `wait_until` | `load`  | `WaitUntil`: `load`, `domcontentloaded`, `networkidle`      |
| `delay`      | `0`     | Milliseconds after the wait, `0..10000`                     |
| `format`     | `png`   | `ImageFormat`                                               |

- File output: image bytes, filename `{host}.{ext}`.
- JSON output: `{data, content_type, width, height, url, status}`; `url` is the final URL after redirects, `status`
  the main document HTTP status.
- Destination receipts are unchanged.

### Contract

`server/types/screenshot.py`: `ScreenshotRequest`, `ScreenshotOptions`, `ScreenshotResult`, `WaitUntil`.

```python
class BrowserService:
    async def screenshot(self, url: str, target: Path, options: ScreenshotOptions) -> ScreenshotResult
    async def aclose(self) -> None
```

## Errors

No new codes.

| Case                                                      | Error                      |
|-----------------------------------------------------------|----------------------------|
| Top-level host denied by policy, `http.enabled: false`    | `403 source_forbidden`     |
| DNS failure, connection refused, TLS error on navigation  | `502 source_fetch_failed`  |
| Main document `4xx`/`5xx`                                 | Screenshot taken, `status` in the result |
| Navigation and capture exceed 30s                         | `504 processing_timeout`   |
| Renderer or browser crash, launch failure (sandbox)       | `500 processing_failed`    |

## Configuration

```yaml
browser:
  sandbox: true            # false only where the runtime blocks user namespaces; logs a warning
```

Concurrency, timeout and recycle count are code heuristics like the other services.

## Spike

`full` tests in the image, gate before the route ships:

1. Playwright launches the headless shell as uid 10001 with `chromium_sandbox=True` under Docker's default seccomp
   profile; record what the target orchestrator needs.
2. All page traffic reaches the proxy: subresources, XHR, redirects, WebSocket, loopback URLs; QUIC and WebRTC
   produce no direct UDP.
3. A rebinding resolver stub (public first, private on connect) never yields a private connection.
4. Infinite JS loop and heap bomb pages fail their request; the next request succeeds on the same browser.
5. Killing the browser mid-request fails that request; the next one relaunches.
6. No zombie processes after 200 requests and a recycle under `tini`.

## Tests

- `utils/proxy.py` and `utils/net.py`: host unit tests against local asyncio servers (tunnel, forward, pin, deny,
  rebinding stub).
- Browser and route: `full` end to end tests against a local fixture HTTP server (allowed with `allow_private` in the
  test config), covering options, full page cap, status passthrough, error mapping.

## Out of Scope

- HTML to PDF as a public route, uploaded HTML, cookies, custom headers, authentication, element clips, JS
  injection, ad or cookie banner blocking.
- Firefox and WebKit engines.
- Playwright on a Windows host (needs the Proactor loop, `--reload` uses Selector): browser work runs in the dev
  container.
