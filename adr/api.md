# API Contract

Uniform request and response shapes for every operation, and an OpenAPI document that describes all fields and
replies. Extends the request contract and outputs of `adr/architecture.md`; operation ADRs only define their options
and data sections.

## Goals

- One response shape for every operation and output mode; switching `output` or adding `destination` never changes
  which facts a client gets back, only where the heavy content lives.
- Symmetric information: what came in (`source`), how it was processed (`options`), what came out (`result`).
- OpenAPI is the client contract: named components, every success and error reply documented, valid for codegen.
- Clean break under `/v1`, released as `v0.3.0` (pre-1.0 minor) with the change in the changelog. No compatibility
  shim.

## Naming

- JSON keys are camelCase in requests, responses and problem bodies (`maxTokens`, `fullPage`, `mimeType`,
  `pageCount`).
- A shared `ApiModel` base in `types/common.py` sets `alias_generator=to_camel`, accepts aliases only (one spelling
  on input) and serializes by alias. Every wire model derives from it; `OpRequest` keeps `extra='forbid'`.
- Validation `loc` paths use the camelCase names.
- Unchanged: enum values (`domcontentloaded`, `o200k_base`), error `code`s (`source_not_found`), `config.yml` keys
  (operator facing, snake_case).
- `mimeType` everywhere; `content_type` disappears from the API.

## Envelope

Every JSON response of an operation has three fixed keys plus op-specific data sections at the top level.

| Key        | Content                                                                                    |
|------------|--------------------------------------------------------------------------------------------|
| `source`   | `Source`: the input document                                                               |
| `options`  | The op options with defaults applied (resolved), always present, `{}` when nothing applies |
| `result`   | `Result`: exactly one primary artifact                                                     |
| `<section>`| Op data sections, e.g. `chunks` for markdown                                               |

- Unknown optional fields are omitted, never `null` (serialization uses `exclude_none` everywhere, chunks included).
- Facts derivable from `options` are not repeated in `result` (no `result.page`).
- Resolved defaults are set on the model, not inside services, so the echo is exact (e.g. render `dpi` becomes `150`
  when no size is given).

### FileInfo

Shared by `source` and `result`.

| Field       | Type   | Notes                                                                           |
|-------------|--------|---------------------------------------------------------------------------------|
| `uri`       | str?   | Source: the request `source`. Result: the `destination`, absent when inline     |
| `name`      | str    | File name (source: materialized name; result: produced name)                    |
| `size`      | int    | Bytes                                                                           |
| `mimeType`  | str    | Explicit format MIME or magika MIME for derived formats (`adr/mime.md`)         |
| `extension` | str    | Normalized extension                                                            |
| `label`     | str    | Papyrus format label or magika label; the language for code                     |
| `group`     | enum   | `FormatGroup`                                                                   |
| `pageCount` | int?   | Pages or frames, when the op knows it (render and thumbnail of PDF and images, OCR) |
| `width`     | int?   | Pixels, raster images only (source images, image results)                       |
| `height`    | int?   | Pixels, raster images only                                                      |
| `title`     | str?   | Document title, when extracted (markdown)                                       |

- `Source` = `FileInfo` + `fetch?: {url, status}` for http(s) sources: final URL after redirects and HTTP status of
  the document. The file fields of `Source` are optional because a navigated page (`/v1/screenshot`) has no file;
  every materialized source fills them.
- `Result` = `FileInfo` + `encoding?` (`Encoding`: `utf-8` | `base64`) + `content?`. All file fields are required.
- Result `label`, `group`, `mimeType`, `extension` come from the format table by label, never from detection.
- Inline content is raw base64, not a data URI: it keeps one `content` field for text and binary, documents as
  `contentEncoding: base64` + `contentMediaType`, and server side consumers decode without parsing.
  todo: opt-in data URI if a front-end consumer needs it.

### Artifacts and data

- An **artifact** is a produced file: it has a `FileInfo`, can be delivered and returned as `output=file`. `result`
  is always the one primary artifact.
- **Data** is structured information about the content (chunks today; bbox, links, page structure later): JSON only,
  typed per op, in top-level sections. Additional artifacts, if ever needed, are a data section listing `FileInfo`
  entries (additive).
- Composing several operations in one call belongs to the future `/v1/pipeline`, where one step's `result` feeds the
  next step's `source`. todo: `results[]` only if a single-op multi-artifact need appears.

## Output Modes

The **op file** is the artifact itself, or, when the response has data sections, the full JSON document (the exact
inline JSON body, `result.content` included). The stored file is then self-describing.

| Mode          | Response                                                                                       |
|---------------|------------------------------------------------------------------------------------------------|
| `output=json` | The envelope with `result.encoding` + `result.content` and data sections                      |
| `output=file` | The op file, its media type and `Content-Disposition` filename                                |
| `destination` | The op file is uploaded; the response is the light envelope: `result.uri` set, no `content`, no data sections |

- With data sections the op file is `<stem>.json` (`application/json`, group `data`), so chunked markdown with
  `output=file` returns the JSON document. The "chunking requires `output=json`" rule is removed.
- With `destination` the response never carries heavy content.
- todo: metadata for `output=file` responses (e.g. image size in headers) on demand.

## Operations

| Op                        | `source` extras                         | `result`                                   | Data       |
|---------------------------|-----------------------------------------|--------------------------------------------|------------|
| `/v1/markdown`            | `title`; with `readMode: ocr` `pageCount`, `width`/`height` (images) | `<stem>.md`, `text/markdown`, `utf-8`      | `chunks`   |
| `/v1/thumbnail`           | `pageCount`, `width`/`height` (images)  | `<stem>.<ext>`, `width`, `height`, `base64`| none       |
| `/v1/render`              | `pageCount`, `width`/`height` (images)  | `<stem>-<page>.<ext>`, `width`, `height`, `base64` | none |
| `/v1/screenshot`          | `uri`, `fetch` only                     | `<host>.<ext>`, `width`, `height`, `base64`| none       |
| `/v1/inspect`             | none                                    | none                                       | `checks`   |

- Ops that produce no artifact (`/v1/inspect`, `adr/inspect.md`) omit `result` and reject `output` and
  `destination`; their findings are data sections and their response model is not an `OpResponse` subclass.

- `pageCount` stays omitted for office and HTML render input (only the requested page is exported).
- Response models are explicit subclasses of `OpResponse` per op with typed `options`: `MarkdownResponse`,
  `ThumbnailResponse`, `RenderResponse`, `ScreenshotResponse`.
- `api/output.py` builds the envelope for all three modes; coordinators provide the source facts, the resolved
  options, the result facts and the data sections. `Transfer.materialize` exposes the fetch facts.

Example, `/v1/render` with `destination`:

```json
{
  "source": { "uri": "s3://prod/docs/report.pdf", "name": "report.pdf", "size": 482113,
              "mimeType": "application/pdf", "extension": "pdf", "label": "pdf", "group": "document",
              "pageCount": 12 },
  "options": { "page": 3, "dpi": 150, "format": "png" },
  "result": { "uri": "s3://prod/pages/report-3.png", "name": "report-3.png", "size": 210334,
              "mimeType": "image/png", "extension": "png", "label": "png", "group": "image",
              "width": 1240, "height": 1754 }
}
```

## Health

- `GET /`: `{status, name, version, watermark?}`; `GET /health` and `GET /ready`: `{status}`.
- `status` is `Literal['ok']`. `/ready` documents `503` with `Problem` (`not_ready`).

## Errors

The problem+json shape of `adr/architecture.md` is unchanged except for camelCase `loc` in `errors`.

- `Problem` component (`utils/problem.py`): `type`, `title`, `status`, `detail?`, `code`,
  `errors?: [{loc, msg, type}]`.
- `code` is an enum generated from the `ProblemError` catalog plus the handler codes of `utils/problem.py`
  (`not_found`, `method_not_allowed`, `not_ready`, `internal_error`), so new errors appear without doc edits.
- Every `/v1` route can return a shared base set (`OP_ERRORS`): `400 bad_request`, `401 unauthorized`,
  `403 source_forbidden`, `404 source_not_found`, `413 payload_too_large | source_too_large`,
  `415 unsupported_media_type`, `422 validation_error | unsupported_uri | unknown_connector`,
  `500 processing_failed | internal_error`, `502 source_fetch_failed | destination_failed`, `503 queue_full`,
  `504 processing_timeout | deadline_exceeded`. It is documented once, in the Error codes guide, and covered on every
  operation by `4XX` and `5XX` Problem responses (OpenAPI status ranges; explicit statuses take precedence) instead
  of being repeated per route, so each reference page shows the errors that matter for it.
- Routes list only their own errors, as error classes: markdown `UnsupportedFormat`,
  `ImageTooLarge`, `TooManyPages`, `OcrNotConfigured`, `OcrFailed`, `OcrRateLimited`; thumbnail and render
  `UnsupportedFormat`, `ImageTooLarge`, `PageOutOfRange`; screenshot none.
- A listed status carries every code it can return, shared ones included (render `422`: `page_out_of_range`,
  `validation_error`, `unsupported_uri`, `unknown_connector`). Headers come from the error classes' `headers()`;
  shared headers (`WWW-Authenticate` on `401`, `Retry-After` on `503`) are documented in the guides.
- Framework rejections and `/ready` have catalog classes too (`BadRequest`, `NotReady`) so they are documented;
  `/ready` renders `NotReady` without raising it, so busy replicas do not log a warning on every probe.

## OpenAPI

- Named components only, no anonymous inline schemas; `inline_refs` is deleted.
- Responses: native `response_model=<Op>Response`; returning a `Response` directly still skips re-serialization.
- Request bodies are parsed manually, so `openapi_extra` references them by `$ref`; `openapi_body` and
  `problem_responses` record the models they reference and an `app.openapi()` override (`install_openapi`) adds them
  with pydantic `models_json_schema` and `#/components/schemas/{model}` refs. Models FastAPI also registers from the
  responses (op options, enums) take the validation schema, so request defaults stay documented.
- `200` content: `application/json` -> the op response model; file media types (`text/markdown`, `application/json`
  for data sections, `ImageFormat` MIMEs) as `format: binary` or string, with the `Content-Disposition` header.
- Multipart: `metadata` is a `$ref` to the request model with `encoding: {metadata: {contentType: application/json}}`,
  plus the binary `file` part.
- `Result.content` documents `contentEncoding: base64` for binary results via the field description and `Encoding`.
- `openapi.json` is never committed; `scripts/openapi.py` builds the app and prints the spec (or writes it to a path)
  for the docs bundle.

## Validation

- `openapi-spec-validator` (dev dependency) validates the generated spec in a test.
- The same test asserts every `/v1` route lists exactly its own statuses plus `4XX` and `5XX`, and that no
  operation uses an inline object schema.
- API tests assert the envelope per op and mode: inline, `output=file`, `destination` (light, no `content`),
  chunked markdown to file and destination as the JSON document, camelCase in and out, snake_case input rejected.
- Bruno requests and examples move to camelCase and the new shapes (`AGENTS.md` repository rules).

## Out of Scope

- Header metadata for file responses, data URI content, multi-artifact `results[]`.
- Source dimensions or page counts that need an extra read in ops that do not already open the file.
