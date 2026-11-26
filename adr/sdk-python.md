# Python SDK

Official Python client of the Papyrus API, published on PyPI as `papyrus-sdk`. It wraps the contract of `adr/api.md` and adds no behavior of its own.

Reference for the rules shared by every SDK: options, errors, retries, probes and versions. `adr/sdk-typescript.md` lists only its differences.

## Goals

- One typed method per operation, sync and async, with the same response shape whatever the operation.
- Complete typing: responses, resolved options and enums are typed for type checkers and editor hints.
- Server and SDK cannot drift unnoticed: the SDK is tested against a running server and the OpenAPI document it serves.
- The SDK is tested on its own, as its users run it: own environment, no server code, every supported Python.
- Minimal footprint for whoever installs it: two dependencies, Python versions older than the server's.

## Decisions

| Concern | Choice |
|---|---|
| Implementation | Handwritten, own models. Not generated (polymorphic responses and multipart with JSON `metadata` generate poorly), not sharing `server/types` (server models are strict on input: `extra='forbid'`, bounds, validators) |
| Location | `sdk/python/` in this repository, next to `sdk/typescript/`, standalone project. No uv workspace: it would resolve on the server's `requires-python` and couple the Dockerfile to the SDK |
| Names | Distribution `papyrus-sdk`, import `papyrus_sdk` (`papyrus` on PyPI is an unrelated package) |
| Python | `>=3.11` |
| Dependencies | `httpx>=0.27`, `pydantic>=2.7`, low lower bounds, no upper bounds |
| Build | `hatchling`, dynamic version from `papyrus_sdk/version.py` (`uv_build` has no dynamic version) |
| Clients | `Papyrus` and `AsyncPapyrus`, native, on `httpx.Client` and `httpx.AsyncClient` (asyncio only) |
| Version | Same `vX.Y.Z` tag as the server |
| License | Apache-2.0, as the repository (`feat/license`) |

## Layout

```
sdk/python/
  pyproject.toml
  uv.lock
  README.md          PyPI page
  LICENSE, NOTICE
  test/              every SDK test, no __init__.py
  papyrus_sdk/
    __init__.py      public names
    client.py        Papyrus, AsyncPapyrus: I/O and retry loop only
    wire.py          request building, response parsing, retry decision; pure, shared by both clients
    models.py
    errors.py
    version.py       __version__ = '0.0.0'
    py.typed
```

- Own uv project with its own `uv.lock` and a `dev` group (`pytest`; `anyio` comes with `httpx`). Its pytest config lives in `sdk/python/pyproject.toml`. The root `pyproject.toml` drops `sdk/python/test` from `testpaths` and `sdk/python` from `pythonpath`, so the server suite never imports the SDK.
- Tests import nothing from `server/` or the root `test/`: fixtures (server under test, OpenAPI document, both clients) live in `sdk/python/test/conftest.py`, the `Server` and `problem` helpers and the documents in `sdk/python/test/support.py`, and `test/fixtures/sdk.py` is deleted. Documents are built by hand (PDFs as in the TypeScript tests), images are checked by magic bytes and reported size: no Pillow or pdfium in the dev group. The sdist excludes `test/`.
- `ruff.toml` stays the only Ruff config, with `[per-file-target-version] "sdk/python/**" = "py311"` so it rejects newer syntax, tests included since they run on 3.11.
- `.dockerignore` excludes `sdk`. Only the Ruff steps of the server check mount `./sdk:/app/sdk:ro`; dekit `docker/test` and the server pytest step no longer do.
- `LICENSE` is a copy of the root file (PEP 639 forbids `..` in `license-files`). `NOTICE` carries product, copyright and license only: the wheel redistributes no third party component and does not ship `adr/license.md`.

## Client

```python
from pathlib import Path
from papyrus_sdk import ChunkingOptions, Papyrus

with Papyrus(base_url='http://127.0.0.1:8000', api_key='dev') as client:
    doc = client.markdown(Path('sample.pdf'), chunking=ChunkingOptions(max_tokens=512))
    doc.result.text
    doc.chunks

    page = client.render(source='https://axelered.com', page=1, format='png')
    page.result.save('page.png')
```

| Parameter | Default | Notes |
|---|---|---|
| `base_url` | `PAPYRUS_BASE_URL`, else `ValueError` | No localhost default: Papyrus is self-hosted |
| `api_key` | `PAPYRUS_API_KEY`, else no header | `ValueError` when it contains a comma: the server variable of the same name is a list, and no valid key contains one |
| `timeout` | connect 5 s, read 310 s | Also per call. The server sends nothing until it is done and `limits.deadline` is 300 s, so `deadline_exceeded` normally comes first; raising `deadline` requires raising `timeout` |
| `max_retries` | 2 | 0 disables retries |
| `http_client` | none | Own `httpx` client for proxies, private CAs and tests |

- Both clients are context managers with `close()` / `aclose()`. They close only the `httpx` client they created.
- The SDK `timeout` also applies to a custom `http_client`, whose 5 s `httpx` default would fail every slow document. Connection limits stay the custom client's own.
- Keys with whitespace, control or non-ASCII characters are rejected too: the HTTP layer would echo them in its error.
- `User-Agent: papyrus-sdk/<version>`. Redirects are never followed, also with a custom `http_client`.
- The key never appears in `repr` or exceptions. `.env` files are not read.

## Operations

| Method | Response | Own parameters |
|---|---|---|
| `markdown` | `MarkdownResponse` | `read_mode`, `chunking` |
| `render` | `RenderResponse` | render options |
| `thumbnail` | `ThumbnailResponse` | thumbnail options |
| `screenshot(url)` | `ScreenshotResponse` | screenshot options |
| `inspect` | `InspectResponse` | none, no `destination` |
| `health` | `StatusResponse` | none |
| `ready` | `bool` | none |

- Options are flat keyword arguments in snake_case. Only arguments that are not `None` are sent, so defaults stay the server's. `ChunkingOptions` is dumped with `exclude_unset`.
- Enum parameters accept the enum or its literal value (`ImageFormat | Literal['png', 'jpeg', 'webp']`).
- The SDK does not validate options: bounds and combinations are the server's `422`.

### Health

- `health()` raises when the server does not answer or answers with an error.
- `ready()` is `False` only for `503 not_ready` and raises otherwise: a wrong `base_url` must not look like a busy server.
- No retries, 5 s timeout. The routes are public, so they do not verify the API key.

### Input

`markdown`, `render`, `thumbnail` and `inspect` take exactly one of the three forms, otherwise `TypeError`:

| Form | `Papyrus` | `AsyncPapyrus` |
|---|---|---|
| positional | `str` with `://` is a URI, any other `str` a local path; `Path`, `bytes`, binary file are uploads | URI `str` or `bytes` |
| `source=` | URI `str`, sent as is, never reads the disk | same |
| `file=` | path `str`, `Path`, `bytes`, binary file | `bytes` |

- `AsyncPapyrus` uploads only `bytes`: paths (positional strings without `://` included) and file objects raise `NotImplementedError` before any I/O, with the alternative in the message. It never touches the disk.
- `filename=` names an upload: default the name of the path or file, else `file`. The extension refines detection (`.csv`, `.rss`). `TypeError` without an upload.
- `screenshot` takes a URL only.
- Schemes and connectors are not validated on the client.
- Risk: with the positional form an untrusted string can make `Papyrus` upload a local file. README and docstrings point to `source=` for input coming from users or LLMs.

### Output

- The SDK always sends `output=json` (`inspect` excluded, it rejects the field) and does not expose `output`: one return type, metadata always present, at the cost of base64 on images of at most 4096 px per side.
- `result.data` is `bytes`, decoded per `encoding`. `result.text` is `str`, `ValueError` unless `utf-8`. `result.save(path)` writes the bytes and returns the `Path`.
- With `destination` there is no content and no `chunks`: the three accessors raise `ValueError` pointing to `result.uri`.
- todo: streaming variants (`output=file`) if artifacts too large for memory appear.

## Models

- `models.py` mirrors the server response types: one response and one options model per operation, `Source`, `Result`, `Fetch`, `Chunk`, checks, and the same `StrEnum`s. snake_case in Python, camelCase on the wire.
- Bounds and validators are not copied. `label` is `str` as on the server (any magika label).
- Unknown fields are ignored. Unknown enum values raise `InvalidResponse`.
- Operation responses carry `request_id` from `X-Request-ID`.

## Errors

```
PapyrusError
  ConnectionFailed     server unreachable, or connection lost
    TimedOut           client side timeout
  InvalidResponse      2xx that is not the expected model
  APIError             status, code, detail, errors, request_id, retry_after
```

- One `APIError` for every problem: `code` is the stable contract, a class per code would add about 20 classes to keep aligned. `code` is `None` when the response is not `problem+json` (e.g. a proxy `413`).
- `httpx` exceptions never escape, they are the `__cause__`.
- Misuse raises built-ins: `TypeError`, `ValueError`, `NotImplementedError`, `FileNotFoundError`.

## Retries

Operations are safe to repeat, but expensive: only failures where the server did no work are retried.

| Failure | Retry |
|---|---|
| `503 queue_full` (rejected before the body is read) | yes |
| `ConnectError`, `ConnectTimeout` | yes |
| `502` / `503` without `problem+json` (proxy, deploys) | yes |
| Any `504`, read timeout | no, same work and likely same outcome |
| `500 processing_failed`, `502 source_fetch_failed`, `502 destination_failed` | no, usually permanent |
| `502 ocr_failed`, `503 ocr_rate_limited` | no, the server already retried and a repeat pays for every page again |
| Connection lost while waiting | no, a document that kills the container would kill it again |

- A proxy can turn a lost connection into a `502`, so the last protection is partial.
- Wait: `max(Retry-After, exponential backoff)` plus jitter, capped at 30 s. Jitter matters because the `Retry-After` of `queue_full` is a constant 2 s.
- File objects are always sent from their beginning, also when partly read (`httpx` rewinds them). Those that cannot seek (pipes) are read into memory: `httpx` would declare a zero length, and a retry could not read them again. A file with a descriptor counts as seekable only when it is a regular file: Windows reports pipes as seekable.
- `keepalive_expiry` is 2 s (not with a custom `http_client`): uvicorn closes idle connections at 5 s, the `httpx` default, and reusing a just closed one would be a lost connection, which is not retried.
- A short loop, no retry library.

## Versioning and Compatibility

- SDK `X.Y` is guaranteed with server `X.Y`. Newer servers work while they only add fields; new enum values require upgrading the SDK. No runtime version check.
- `version.py` keeps the `0.0.0` placeholder in git, as `server/version.py`.

## Tests

`sdk/python/test/`, run from `sdk/python/` with `uv run pytest`. CI runs it on Python 3.11 and 3.14 (`uv run -p`); dekit is a smoke run on the default Python. The server under test comes from `PAPYRUS_BASE_URL` and `PAPYRUS_API_KEY`: unset or unreachable fails, a skip would hide drift.

1. Schema, against `GET /openapi.json` of the server under test: method keywords match the `options` properties, models match the response schemas (`options` included), enums and literals match their values. A field added only to the server fails, naming where to add it.
2. Parity: both clients have the same methods and parameters; only the upload types are narrower in `AsyncPapyrus`.
3. Integration: every operation with `Papyrus` and `AsyncPapyrus` against the server under test, every input form, a non-ASCII file name, real errors, `health` and `ready` (only `True`: a busy server cannot be produced on demand). They rely only on the defaults of a clean server: no OCR backend, no `memory://` storage, the SSRF guard refusing local pages (so `screenshot` checks the refusal).
4. Client with `MockTransport`: retries, timeouts, non `problem+json` errors, input forms, `AsyncPapyrus` refusals, configuration, probes, and what a clean server cannot produce (OCR responses, a successful `screenshot`, `destination` uploads, a `queue_full` retry, `ready()` at capacity).
5. Unknown fields are ignored, unknown enum values raise `InvalidResponse`.
6. Wheel, in CI only: `uv build`, install the wheel in a clean 3.11 environment and import it.

- The server under test is the runtime image with authentication on and no config file: dekit `sdk/server` (`papyrus` image, port 8003, `PAPYRUS_API_KEY=dev`, deps `docker/build-prod`), the same in CI.
- dekit task `sdk/python`: deps `sdk/server`, `PAPYRUS_BASE_URL=http://localhost:8003`, `PAPYRUS_API_KEY=dev`, runs `uv run --directory sdk/python pytest`, i.e. 1 to 5 once. No shell script: dekit runs on Windows too.
- todo: the CI job (3.11 and 3.14, wheel check) is specified with the CI/CD rework in `adr/development.md`.

## Documentation

- `sdk/python/README.md`: install, a sync and an async example, `source=` versus `file=` with the untrusted input warning, errors.
- `docs/python.mdx` in the SDKs group, with `docs/meta.json` and the structure table of `adr/docs.md`. Docs go out with every tag, so the SDK must be on PyPI before the next one: until then `pip install papyrus-sdk` gets an empty `0.0.0` placeholder.
- Bruno is unaffected, the API does not change.

## Out of Scope

- todo: publishing. Stamp `sdk/python/papyrus_sdk/version.py` in the semver step; publish with PyPI Trusted Publishing (needs the publisher configured on PyPI, environment `pypi`), before the GitHub release.
- todo: `check.yml` runs on tags only (`push: main` and `pull_request` are commented out), so drift surfaces at release time. Owned by the user.
- todo: add `hatchling` (MIT) to the development table of `adr/license.md` once `feat/license` is merged.
- todo: disk uploads in `AsyncPapyrus`, multiple file parts, `GET /`.
- SDKs in languages other than Python and TypeScript (`adr/sdk-typescript.md`).
