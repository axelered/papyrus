# Browser Engine

Headless Chromium for content that needs a real browser: `POST /v1/screenshot` of live web pages now, full-fidelity
HTML rendering later. Extends the service kinds of `adr/architecture.md`; image output uses the shared
`ImageFormat` and `MAX_IMAGE_SIDE` of `server/types/common.py` (also used by `adr/render.md`).

## Goals

- Untrusted pages, including JavaScript, can never reach a destination the `http.*` policy denies: every
  connection is resolved, checked and pinned by Papyrus, closing the DNS rebinding gap for the browser.
- One long-lived browser per container, isolated contexts per request, no process spawned per request.
- A hostile page fails its own request (renderer crash, timeout) and never takes down the container.
- No TCP debug port, no runtime downloads. Chromium launches at boot with `workers.warmup`, lazily without it
  (`adr/performance.md`, Warm services).
- HTML thumbnails (`adr/render.md`, HTML Thumbnails) screenshot materialized HTML through the Documents mechanism.
- todo: HTML in `/v1/render` through Chromium (`pdf()` print to PDF, then pdfium); uploaded HTML for
  `/v1/screenshot`.

## Decision

Async Playwright driven from the API process, one supervised browser, one `BrowserContext` per request.

| Option                                  | Verdict                                                                     |
|-----------------------------------------|-----------------------------------------------------------------------------|
| Async Playwright in the API process     | **Chosen**: Chromium does the work out of process, the event loop only awaits; semaphore capacity like the Remote kind |
| Pebble pool, sync Playwright per worker | Rejected: killing a worker on timeout can orphan the driver and Chromium, N browsers cost N x 150 MB+ |
| Raw CDP to Debian `chromium`            | Rejected: reimplements Playwright                                           |
| Sidecar (browserless, Gotenberg)        | Rejected: second container and deployment unit (same as `adr/convert.md`)   |
| LibreOffice HTML import                 | Kept for HTML in `/v1/render` until it moves to Chromium                    |

The model mirrors Gotenberg (one supervised Chromium, context per conversion, restart after N conversions, internal
DNS pinning proxy), which validates the egress design below.

## Stack

| Concern  | Choice                                                                                       |
|----------|----------------------------------------------------------------------------------------------|
| Driver   | `playwright` (main dependency, wheel bundles the Node driver)                                |
| Browser  | `chrome-headless-shell` via `playwright install --with-deps --only-shell chromium` at Docker build into `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright`, run from a lock-only `deps` stage venv bind-mounted into `base` so code changes never invalidate the layer |
| Fonts    | Installed by `--with-deps`: liberation, noto color emoji, CJK (wqy-zenhei, ipafont), thai     |
| Init     | `tini` as image entrypoint (see Process Model)                                               |
| Egress   | `utils/proxy.py` pinning forward proxy in the API event loop                                 |
| Capture  | Raw CDP `Page.captureScreenshot` on a session of the page: Playwright cannot encode webp      |

## Process Model

```
tini (PID 1)
└─ uvicorn: event loop, routes, PinningProxy coroutines on 127.0.0.1:<random> (one per request, one deny-all)
   ├─ pebble pools (unchanged)
   └─ node: Playwright driver, stdin/stdout pipe to Python, spawned on first screenshot
      └─ chrome-headless-shell browser, CDP over --remote-debugging-pipe (no TCP port)
         ├─ zygote, network service, storage service, GPU process (software)
         └─ renderers: one per site per context, created and destroyed with pages
```

- `tini` is required: Chromium churns short-lived processes and orphaned descendants are reparented to PID 1;
  uvicorn never reaps them. `ENTRYPOINT ["tini", "-s", "--"]` (in `base`, so dev and runtime) also forwards signals;
  `-s` registers it as subreaper so `docker run --init` (dekit, CI) nests cleanly. It also serves the future clamd
  multi-process entrypoint.
- If Python dies hard the chain ends by pipe EOF (the driver exits on stdin close, Chromium on CDP pipe close).

### Lifecycle

| Event                    | Handling                                                                        | Result |
|--------------------------|---------------------------------------------------------------------------------|--------|
| First call               | `async_playwright().start()` then `chromium.launch(args, proxy)` under an `asyncio.Lock` | One launch even under concurrent first calls |
| Request                  | `Semaphore(concurrency)` -> `new_context` -> `goto` -> optional delay -> `screenshot` -> `context.close()` in `finally` | Renderers exit with the context |
| Timeout, deadline cancel | Operation under `asyncio.timeout(30)`, then `context.close()` (kills renderers stuck in JS loops) | `504`, browser stays healthy |
| Renderer crash           | Page `crash` event                                                              | `500` for that request only |
| Browser or driver crash  | In-flight Playwright calls fail; the next acquire sees `is_connected()` false and relaunches; a failing launch retries once with a fresh driver | In-flight `500`, next call relaunches lazily (no restart loop) |
| Recycle after 200 contexts | Launch a fresh browser for new contexts, close the old one when its in-flight count reaches 0 | No killed requests |
| Shutdown                 | `aclose()`: `browser.close()`, `playwright.stop()`, proxy close                 | Within the 30s grace period |

### Sizing

- Concurrency: `max(1, workers.processes // 2)` contexts (convert heuristic).
- `--js-flags=--max-old-space-size=512` caps the V8 heap per renderer: a memory bomb crashes its renderer (`500`)
  instead of OOM-killing the container with the pebble pools.
- Full-page captures are capped at 4096 px height (`adr/render.md`), so infinite scroll cannot exhaust memory.
- Memory (measured, summed RSS of driver and browser, shared pages overcounted): about 325 MB idle, about 100 MB per
  open simple page, real sites more. Image grows by about
  1.1 GB (measured: headless shell 287 MB, system libraries and fonts 327 MB, Python package with the Node driver
  165 MB). todo: install a hand-picked dependency list instead of `--with-deps` if image size matters.

### Sandbox

- Off by default (`browser.sandbox: false`): the Chromium sandbox needs unprivileged user namespaces and Docker's
  default seccomp profile blocks them (`unshare -U` gives EPERM, the spike confirmed it; Kubernetes `RuntimeDefault`
  and Ubuntu 24.04+ AppArmor restrict them too). It works under `seccomp=unconfined`.
- Mitigations without it: non-root uid 10001, every connection through the pinning proxy, V8 heap cap, fresh context
  per request, image rebuilt with current Chromium. Remaining risk: a renderer exploit runs as the API user and can
  read `PAPYRUS_API_KEY`, connector secrets and other workspaces.
- `browser.sandbox: true` where the runtime allows user namespaces; a blocked sandbox then fails the launch loudly
  (`500 processing_failed`), never a silent fallback. The launch log line states the sandbox mode.
- todo: ship a seccomp profile (Docker default plus user namespaces) and turn the sandbox on by default.

## Egress

Every TCP connection of the browser goes through a pinning proxy; UDP paths are disabled.

- `server/utils/proxy.py`: generic `PinningProxy(check, on_reject)` asyncio forward proxy, `start() -> port`,
  `aclose()`. Handles `CONNECT host:port` tunnels (https, wss; TLS stays end to end, never decrypted) and absolute-URI
  `http://` forwarding (one request per client connection, sent with `Connection: close`). `check(host, port)` returns
  the address to connect to (the pin) and raises `PermissionError` to deny (`403`); other failures and refused
  upstreams answer `502`. `on_reject(host, port, status)` reports every `403`/`502`.
- `server/utils/net.py`: `HostPolicy(allowed_hosts, allow_private, resolver)` with `check` (validate) and `resolve`
  (validate and return the pin), moved out of `io/http.py` (which uses it, behaviour unchanged). Every resolved
  address must be public unless `allow_private`; `HostForbidden` subclasses `PermissionError` so `utils/proxy.py`
  needs no `server.*` import.
- `services/browser.py` starts one proxy per request with `HostPolicy.resolve` as `check` and passes it as the
  context proxy, so rejections are attributed to that request without credentials. A rejected top-level navigation
  (tracked from main-frame navigation requests, so redirects count) becomes `403 source_forbidden` or
  `502 source_fetch_failed`; a rejected subresource just fails inside the page. For plain http the rejection arrives
  as the proxy's own `403`/`502` response and is mapped the same way.
- The browser is launched with a deny-all proxy, so traffic outside a request context has no route out.
- The source host is checked before the browser is used, so denied sources never start Chromium.
- Chromium flags: `--disable-quic`, `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`,
  `--webrtc-ip-handling-policy=disable_non_proxied_udp`, `--disable-dev-shm-usage`. Loopback URLs go through the
  context proxy without an explicit `<-loopback>` bypass rule (verified by the spike).
- Context options: `service_workers='block'`, `accept_downloads=False`. Navigation from a page to `file:` is blocked
  by Chromium itself (verified by the spike).
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
| `fullPage`   | `false` | Capture height capped at 4096 px                            |
| `scale`      | `1`     | `device_scale_factor`, `1..3`                               |
| `waitUntil`  | `load`  | `WaitUntil`: `load`, `domcontentloaded`, `networkidle`      |
| `delay`      | `0`     | Milliseconds after the wait, `0..10000`                     |
| `format`     | `png`   | `ImageFormat`                                               |

- File output: image bytes, filename `{host}.{ext}`.
- JSON output: the envelope of `adr/api.md`; `source` holds only `uri` and `fetch` (`url` final URL after
  redirects, `status` main document HTTP status), `result` the image `width`, `height` and `base64` content.

### Documents

`screenshot(url, target, options, document=None)`: with `document`, the bytes of a workspace file answer the main
navigation of `url`, so HTML from uploads, connectors or a fetch renders without ever being loaded from disk by
Chromium.

- Never `file://`: a `file://` page can display other local files (`<iframe src="file:///proc/self/environ">`,
  `<img src="file:///...">`) even though `fetch` is blocked, which would leak secrets and other workspaces into the
  image. An http(s) origin cannot load local resources at all.
- Python reads the file and passes the bytes over the Playwright pipe; Chromium gets no path and no
  `--allow-file-access*` flag.
- One context route (a match-all predicate: string patterns are globs, so `?` in a URL would act as a wildcard)
  serves the document as the first main-frame navigation, whatever URL Chromium normalized `url` to; later requests
  to that URL (iframe, fetch replay) are aborted. The route exists only for documents, so plain screenshots keep
  uninterrupted networking.
- In the same route every request with a scheme other than `http`, `https`, `data` or `blob` is aborted (defense in
  depth over Chromium's own web to `file:` block: `file:`, `chrome:`, `devtools:`, `view-source:`, `filesystem:`).
- Verified by `full` tests: `file://` iframes, images, embeds, fetch, `<base>`, `location`, meta refresh, popups and
  `view-source:` leak nothing; a positive control proves the frame text check would see an embedded marker.
- The origin is never client chosen: the fetched source URL, or `http://document.invalid/<name>` (reserved name, never
  resolves) for uploads and connectors. Relative references then fail at the proxy (`502`), absolute ones follow the
  `http.*` policy like any page.
- With `document` the source pre-check is skipped: the fetcher already applied the policy, or the origin is the
  synthetic one; the per-request proxy, timeout and error mapping are unchanged.
- Risk accepted: uploads and connector files can now run JS in Chromium (before, only pages a caller could name);
  the unsandboxed 0-day risk is unchanged and tracked by the sandbox todo.

### Contract

`server/types/screenshot.py`: `ScreenshotRequest`, `ScreenshotOptions`, `ScreenshotResponse`, `WaitUntil`.

```python
class BrowserService:
    async def screenshot(
        self, url: str, target: Path, options: ScreenshotOptions, document: Path | None = None
    ) -> Capture  # width, height, url, status
    async def aclose(self) -> None
```

- The capture clip carries `scale`: `Page.captureScreenshot` from a second CDP session resets Playwright's device
  metrics emulation, so the layout runs at the emulated ratio and the clip renders at it; the page is discarded after.

## Errors

No new codes.

| Case                                                      | Error                      |
|-----------------------------------------------------------|----------------------------|
| Top-level host denied by policy, `http.enabled: false`    | `403 source_forbidden`     |
| DNS failure, connection refused, TLS error on navigation  | `502 source_fetch_failed`  |
| Main document `4xx`/`5xx`                                 | Screenshot taken, `source.fetch.status`  |
| Navigation and capture exceed 30s                         | `504 processing_timeout`   |
| Renderer or browser crash (also mid-navigation), launch failure (sandbox) | `500 processing_failed` |

## Configuration

```yaml
browser:
  sandbox: false           # true only where the runtime allows unprivileged user namespaces
```

Concurrency, timeout and recycle count are code heuristics like the other services.

`http.locale` (`adr/architecture.md`, Request Identity) is passed as `new_context(locale=…)`: it sets
`Accept-Language`, `navigator.language` and `Intl` formatting, so pages that pick their language in JavaScript
follow it too. `null` keeps the Playwright default. The header carries the bare tag (`it-IT`), unlike the fetcher's
`it-IT,it;q=0.9`: the override wins over `extra_http_headers`, so the two are not aligned.

> [!NOTE]
> The user agent stays Chromium's own (`HeadlessChrome/…`). Replacing `HeadlessChrome` with `Chrome` in the context
> `user_agent` defeats only naive checks, since bot detection fingerprints in JavaScript; reconsider if blocked
> screenshots show up in practice.

## Spike

Run as `full` tests in the image (`test/services/test_browser.py`):

1. Failed as planned: Chromium 159 headless shell launches as uid 10001 under `tini`, but `chromium_sandbox=True`
   fails under Docker's default seccomp profile; works with `seccomp=unconfined`. Decision: sandbox off by default
   (see Sandbox).
2. Passed: subresources, fetch to `127.0.0.1` and `localhost`, WebSocket and redirects all reach the proxy; nothing
   reaches the fixture server under a denied name.
3. Passed: a resolver answering public on check and loopback afterwards never yields a connection.
4. Passed: an infinite loop times out (`504`), a heap bomb crashes its page (`500`); the next request succeeds.
5. Passed: killing the browser mid-request fails it with `500`, the next request relaunches.
6. Passed: recycling every 2 contexts leaves one browser, no zombies, and no Chromium process after `aclose()`.

## Tests

- `utils/proxy.py` and `utils/net.py`: host unit tests against local asyncio servers (tunnel, forward, pin, deny,
  rebinding stub).
- Browser and route: `full` end to end tests against a local fixture HTTP server (allowed with `allow_private` in the
  test config), covering options, full page cap, status passthrough, error mapping.
- Documents: uploaded HTML with `file://` iframes, images, fetch, meta refresh, `location` and `<base>`, replays of
  the origin, relative and denied absolute references; asserts only allowed hosts are contacted, the page stays on
  the origin and a workspace marker file never appears in any frame's text.

## Out of Scope

- HTML to PDF as a public route, uploaded HTML for `/v1/screenshot`, cookies, custom headers, authentication,
  element clips, JS injection, ad or cookie banner blocking.
- Firefox and WebKit engines.
- Playwright on a Windows host (needs the Proactor loop, `--reload` uses Selector): browser work runs in the dev
  container.
