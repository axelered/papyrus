# Inspect

`POST /v1/inspect` reads a source and returns its standardized file facts and a set of checks, without producing an
artifact. Reuses materialization (`adr/architecture.md`) and detection (`adr/mime.md`); extends the envelope rules of
`adr/api.md`.

## Goals

- One cheap call answering "what is this file": the same `Source` facts every other op reports.
- Flag files whose name disagrees with their content before a client queues heavy work.
- Inline and fast: no worker pool, no format allowlist, every source inspectable.

## Flow

```
op_context -> transfer.materialize -> detect.detect -> checks -> InspectResponse
```

- Route in `server/api/inspect.py`, tag `inspect`, same `op_context` (admission slot, deadline, size limits) and both
  transports (JSON and multipart) as every op.
- The source is fully materialized: magika reads head and tail, so a partial fetch would save little and add a path.
- No route-specific errors; only the shared `4XX` and `5XX` set. Unknown content is a valid answer
  (`label: unknown`), never `415`.

## Contract

### Request

`InspectRequest`: `source` and `options` only, on `SourceRequest`, the `source`-only base that `OpRequest` extends with
`destination` and `output`. Sending either is a `422 validation_error` (`extra='forbid'`). `options` is an empty `OptionsModel` today, kept so later checks can add
switches without changing the shape.

### Response

`InspectResponse(source, options, checks)` is its own `ApiModel`, not an `OpResponse` subclass: there is no artifact,
so there is no `result`.

| Key       | Content                                                     |
|-----------|-------------------------------------------------------------|
| `source`  | `Source` from `source_of`, identical to other ops           |
| `options` | Resolved options, `{}` today                                |
| `checks`  | `InspectChecks` data section                                |

```json
{
  "source":  { "uri": "https://axelered.com/report.pdf", "name": "report.pdf", "size": 48211,
               "mimeType": "text/html", "extension": "html", "label": "html", "group": "markup",
               "fetch": { "url": "https://axelered.com/report.pdf", "status": 200 } },
  "options": {},
  "checks":  { "extensionMatch": false }
}
```

### Checks

- Every check is a boolean: `true` passed, `false` failed, omitted when the check does not apply.
- Facts (`pageCount`, `width`, `height`, future `encrypted`) belong in `source` as `FileInfo` fields; `checks` only
  answers whether something passed.
- The file is never rejected for a failed check; the client decides.

| Check            | Rule                                                                                         |
|------------------|----------------------------------------------------------------------------------------------|
| `extensionMatch` | Uses the materialized name's extension, before normalization. `true` when it maps (with aliases) to the detected format or to a generic ancestor of it (`notes.txt` with Python, `photo.jpeg`, `book.docx`). `bin` declares `unknown`. `false` when it maps to another format or to none (`report.pdf` that is HTML, `data.xyz`). Omitted when the name has no extension |

- The ancestor test reuses `_ancestors` through `extension_match` in `server/services/detect.py`.
- http(s) URLs without an extension are named after the response `Content-Type` (`server/io/http.py`), so for them
  `extensionMatch` compares the content with the declared `Content-Type`; a server labelling a PDF `text/html` is
  flagged, which is the point of the check.

## API Contract Changes

`adr/api.md` gains one rule and one operations row:

- Ops that produce no artifact omit `result` and reject `output` and `destination`; their findings are data sections.
- `/v1/inspect`: `source` standard, no `result`, data `checks`.

## Validation

- API tests: upload, http(s) and connector sources; matching, aliased, generic-ancestor, mismatching, unknown and
  missing extensions; unknown content returns `200`; `output` and `destination` rejected with `422`.
- The OpenAPI test covers the route like any op (own statuses only plus `4XX` and `5XX`, no inline schemas).
- Bruno: endpoint doc and examples for upload, `https://axelered.com` URL, connector source and a mismatching
  extension; a short entry in the guides.

## Out of Scope

- Structural probe and a validity verdict: pdfium page count and encryption, Pillow `verify` for size and
  truncation (both in the render pool), zip central directory, members and compression ratio, strict UTF-8 and JSON
  parse; no verdict for legacy office and `msg`. todo: tracked in `AGENTS.md`.
- `sha256` in `FileInfo`, which belongs to every op. todo: tracked in `AGENTS.md` under API Contract.
- A separate check on the declared HTTP `Content-Type` or multipart part type.
- Partial or head-only fetches.
