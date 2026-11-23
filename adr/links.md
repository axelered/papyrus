# Shared Links

Public share links of drives and code hosts (Google Drive, Dropbox, OneDrive) point to a viewer page, not to the
document. Link resolvers recognize these URLs and fetch the file behind them, so every materialized http(s) source
is processed as the document it names.

## Goals

- A share link behaves as the file it shares on every endpoint that materializes a source (`/v1/markdown`,
  `/v1/thumbnail`, `/v1/render`); detection and routing stay content-based (`adr/mime.md`).
- A link that cannot be resolved to a file fails explicitly; the viewer or login page is never processed.
- Every request a resolver triggers goes through the same SSRF policy as the source fetch.
- The resolver contract already fits authenticated, API-driven flows (OAuth) without a signature change.

## Scope

- In: public, unauthenticated links to a single file on the providers below.
- Out: `/v1/screenshot` (navigates the URL as a website, unchanged), folders, providers that need API calls or
  expiring tokens (Box, iCloud, WeTransfer), OAuth.
- todo: OAuth as resolvers that call provider APIs (Drive `files.get` + `alt=media`/`export`, OneDrive
  `shares/u!<base64>`) and return a `Target` with an `Authorization` header.

## Components

| Component      | Location              | Role                                                                     |
|----------------|-----------------------|--------------------------------------------------------------------------|
| `SafeClient`   | `server/utils/net.py` | httpx client: host policy on every hop, manual redirects, credential rule |
| `LinkResolver` | `server/io/links.py`  | Protocol plus one class per provider, `LINKS` tuple of instances         |
| `HttpFetcher`  | `server/io/http.py`   | Resolvers + `SafeClient` + saving under size limits                      |

### Contract

```python
@dataclass(frozen=True)
class Target:
    url: httpx.URL
    link: str                                  # resolver name, e.g. 'google-drive'
    headers: Mapping[str, str] = field(default_factory=dict)

class LinkResolver(Protocol):
    name: str
    async def resolve(self, url: httpx.URL, client: SafeClient) -> Target | None: ...  # None = not mine
```

- The resolver describes the final request; the fetcher always performs and saves it. Resolvers never download
  content, so size limits, workspace handling and error mapping live in one place.
- v1 resolvers are pure rewrites and ignore `client`. API-driven resolvers use `client` for intermediate calls.
- todo: `method`/`body` on `Target` only if a provider needs a non-GET final request.

### SafeClient

Extracted from the `HttpFetcher` hop loop.

```python
async def stream(self, url: httpx.URL, headers: Mapping[str, str] = {},
                 intercept: Callable[[httpx.URL], Awaitable[Target | None]] | None = None)
```

- Each hop: `intercept(current)` first, then the `HostPolicy` check, then a request with `follow_redirects=False`.
- On the first `Target` from `intercept`, continue with its URL and headers and stop intercepting: at most one
  resolution per fetch. A redirect chain to a share link (`bit.ly → drive.google.com/...`) resolves on the hop
  that reaches it.
- `http.max_redirects` bounds the whole chain, hops before and after resolution.
- `Authorization` is dropped when a redirect changes origin (scheme, host, port), so no resolver can leak a token.
- Resolver calls through `client` pass no `intercept`: resolvers never re-enter each other.

### HttpFetcher

- Takes `links: Sequence[LinkResolver]` at init; `factory.py` passes `LINKS` when `http.shared_links` is on, `()`
  otherwise.
- `intercept` tries the resolvers in order; the first non-`None` wins.
- `Fetched` gains `link: str | None`, the matched resolver name.
- The local filename comes from `Content-Disposition` (`filename*` before `filename`), then the URL basename, then
  the `Content-Type` extension guess. Applies to every http(s) source.

## Providers

| `link`         | Matches                                                                         | Target                                                              |
|----------------|---------------------------------------------------------------------------------|---------------------------------------------------------------------|
| `google-drive` | `drive.google.com/file/d/<id>/...`, `drive.google.com/{open,uc}?id=<id>`         | `drive.usercontent.google.com/download?id=<id>&export=download&confirm=t` |
| `google-docs`  | `docs.google.com/{document,spreadsheets,presentation}/d/<id>/...`                | `docs.google.com/<kind>/d/<id>/export?format={docx,xlsx,pptx}`      |
| `dropbox`      | `www.dropbox.com/{s,scl/fi}/...`                                                 | same URL with `dl=1`                                                |
| `onedrive`     | `1drv.ms/...`, `onedrive.live.com/...`, `*.sharepoint.com/:<x>:/...`             | same URL with `download=1`                                          |
| `github`       | `github.com/<owner>/<repo>/blob/<ref>/<path>`                                    | `raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>`             |

- Drive `/file/d/` links are uploaded files: the download returns the original bytes and magika detects the type.
  `confirm=t` skips the virus scan interstitial of large files.
- Google native docs have no file; they always export to OOXML: lossless for markdown and chunking (Sheets keep
  their tables), and render routes OOXML through LibreOffice (`adr/convert.md`).
- Folder URLs of a known provider (`drive.google.com/drive/folders/...`, Dropbox `/sh/`, GitHub `tree/`) raise
  `422 unsupported_uri`.
- OneDrive and SharePoint `download=1` works only for "anyone with the link" shares; anything else hits the HTML
  rule below.

> [!NOTE]
> Op-dependent export: the coordinator could pass a preference down (`materialize(..., prefer=PDF)` from render,
> OOXML from markdown) so Docs thumbnails skip LibreOffice and keep Google's layout. Consider it only if native Docs
> thumbnails become a measured bottleneck; it leaks an op concern through `Transfer`, `HttpFetcher` and one resolver.

## Failure

- A matched link whose detected format is HTML raises `403 source_forbidden`: "`<link>` link did not return a file;
  check the file is shared publicly". This covers private files (login page), native docs behind a `/file/d/` link,
  and provider URL changes. The check runs in the coordinators after detection, on `Fetched.link`.
- The rewritten host goes through `http.enabled`, `http.allowed_hosts` and `http.allow_private` like any URL.

## Configuration

```yaml
http:
  shared_links: true   # resolve share links of known providers to the file; false fetches the URL as given
```

- `config.example.yml` documents that an `allowed_hosts` list must include the resolved hosts
  (`drive.usercontent.google.com`, `docs.google.com`, `dl.dropboxusercontent.com`, `raw.githubusercontent.com`,
  ...), not only the hosts users paste.

## API

- `Fetch` gains `link: str | null`, the resolver name; `null` when no resolver matched. Additive, no version bump.
- Bruno: examples with a Drive file and a Google Doc source for `/v1/markdown` and `/v1/thumbnail`, showing
  `fetch.link`, plus the `403` for a private link.

## Tests

- `MockTransport` only (`test/fixtures/http.py`), no live provider calls.
- Per-resolver table of input URL to `Target | None`, including folders and non-matching lookalikes.
- Redirect chain to a share link, hop budget across resolution, `Authorization` dropped on a cross-origin redirect,
  matched link returning HTML, `shared_links: false`, `Content-Disposition` filenames.
- todo: live provider tests in CI against public fixture files, to catch provider URL changes.
