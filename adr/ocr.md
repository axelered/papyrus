# OCR

`readMode: ocr` on `POST /v1/markdown` (`adr/markdown.md`) reads page images with a remote vision model instead of
extracting the text stored in the file. Backends: Mistral OCR and Qwen VLM on any OpenAI compatible server. First
Remote service of `adr/architecture.md`; pages come from `adr/render.md`.

## Goals

- Scanned PDFs, images and image-heavy office files become markdown of the same kind `text` mode produces, chunkable
  by `adr/chunking.md`.
- Page boundary: one page is one image and one model call, whatever the format and backend.
- A `200` means a complete document: no silent holes, no silent truncation.
- No document leaves the container unless the operator configured a backend.

## Request

| Option     | Type            | Default | Rules                                                         |
|------------|-----------------|---------|---------------------------------------------------------------|
| `readMode` | `text` \| `ocr` | `text`  | `ReadMode` `StrEnum` in `types/markdown.py`, combines with `chunking` |

- Named after where the content comes from, not speed or quality: `text` is exact on born-digital files, a model can
  misread digits and names.
- `text` is the behavior of `adr/markdown.md`, unchanged.
- `ocr` means "OCR where the format has pages": every page is read from its image, also when the PDF has a text layer.
- todo: `auto`, OCR only for pages without a usable text layer. Needs thresholds tuned on real documents (scans with
  a poor OCR layer).

## Formats

| Detected label                                                  | `ocr` path                                        |
|-----------------------------------------------------------------|---------------------------------------------------|
| `pdf`                                                           | one render per page                               |
| `tiff`                                                          | one render per frame                              |
| `png`, `jpeg`, `webp`, `gif`, `bmp`, `avif`                     | first frame only, an animation is not a document  |
| `docx`, `doc`, `odt`, `rtf`, `pptx`, `ppt`, `odp`               | whole document to PDF (`ConvertService`), then as `pdf` |
| every other format accepted by `/v1/markdown`                   | falls back to `text`                              |
| anything else                                                   | `415 unsupported_format`                          |

- Spreadsheets fall back: LibreOffice pagination splits wide sheets and cuts cells.
- The fallback depends on the format only. A failed OCR never falls back: a scan would return an empty `200`.
- `options.readMode` in the response is the effective mode (`text` after a fallback), since `options` reports how the
  document was processed (`adr/api.md`). Not visible with `output=file`.
- Images in `text` mode stay `415`, with a `detail` that points to `readMode: ocr`.

## Output

| Element                   | Rule                                                                           |
|---------------------------|--------------------------------------------------------------------------------|
| Tables                    | GFM pipe tables, the only form the chunker keeps whole. Merged cells repeat their value |
| Formulas                  | LaTeX, `$...$` and `$$...$$`                                                   |
| Images, figures           | Omitted, text inside them is kept. todo: descriptions                          |
| Page headers and footers  | Kept (letterheads, VAT and invoice numbers). todo: option to drop them         |
| Stamps, signatures, handwriting | Readable text only                                                       |
| Language                  | As in the document, no translation or correction                               |
| Blank page                | Empty, valid                                                                   |

- `source.pageCount` is set, and `source.width`/`height` for images; `source.title` is omitted.
- Heading levels are decided page by page, so `chunks[].headings` is less reliable than in `text` mode.

### Page Markers

- Each page starts with a marker, also in single-page documents; pages are joined by a blank line.
- Presentations: `<!-- Slide number: N -->`, as markitdown writes it, so `chunks[].slide` works in both modes.
- Others: `<!-- Page number: N -->`, from 1; sets `chunks[].page`, the page the chunk starts on.
- A page marker is not a section boundary (paragraphs continue across pages), a slide marker is. A table across two
  pages stays two tables.
- Markers emitted by the model are stripped.
- LibreOffice may leave hidden slides out of the PDF: N then counts exported slides. todo: confirm.

## Architecture

```
server/ocr/
  base.py       OcrAdapter: read(image: Path) -> str, aclose(), dpi, max_pixels; OcrError hierarchy
  qwen.py       openai SDK, prompt, parameters, cleaning
  mistral.py    mistralai SDK, parameters, cleaning
  clean.py      cleaning shared by adapters (stray markers)
server/services/ocr.py   OcrService: slots, retries, errors to ProblemError
```

- **Layering:** `server/ocr/` imports stdlib and third-party only, like `utils/`; only `services/ocr.py` and the
  wiring import it.
- **Adapter:** wraps one model family: one call, no policy. `type` names the family, not the protocol, because prompt,
  cleaning, resolution and token limits are model biased. A new model is a new adapter. It maps SDK exceptions to
  `OcrTransient`, `OcrRateLimited(retry_after)`, `OcrTimeout`, `OcrTruncated`, `OcrRejected` and knows no
  `ProblemError`.
- **Service:** semaphore, retries and error mapping, identical for every adapter. SDK retries are disabled
  (`max_retries=0`, no `retry_config`), otherwise they multiply with ours and hold the slot while waiting.
- **Imports:** only the SDK of the configured `type`, in the API process, so deployments without OCR pay nothing.
- The client does not use the SSRF guard: the URL comes from the operator, often on a private network.
- New dependencies: `openai`, `mistralai`, `httpx2` (the HTTP client of both SDKs), `tenacity`.
- SDK exceptions are never chained or logged: their messages carry the upstream body, and may quote the document.
- `mistralai` ships tracing that stays off unless `MISTRAL_SDK_TELEMETRY` is set; it must stay unset, spans hold
  content.

### Adapters

|              | `qwen`                                                    | `mistral`                              |
|--------------|-----------------------------------------------------------|----------------------------------------|
| Call         | `chat.completions`, image data URL plus prompt            | `ocr.process`, image data URL          |
| Parameters   | `temperature: 0`, `max_tokens: 8192`, `extra_body` from config | `include_image_base64: false`, `include_blocks: false` |
| Cleaning     | `<think>` block, fence wrapping the whole answer          | References to images of the response   |
| Truncation   | `finish_reason: length`                                   | todo: no signal in the SDK types       |

- Own Qwen prompt: the built-in `qwenvl markdown` emits coordinates and LaTeX tables.
- The openai SDK falls back to `OPENAI_API_KEY` from the environment: always pass a key (a placeholder when none is
  configured) so a foreign key never reaches `url`.
- A null answer (reasoning only, gateway errors sent as `200`) is a transient failure, never a blank page.
- `extra_body` exists because OpenAI compatible servers differ, e.g. vLLM turns Qwen reasoning off with
  `chat_template_kwargs: {enable_thinking: false}`; left on, reasoning eats `max_tokens`.

### Rendering

- `RenderService.page` as is, one page per task: `RenderSpec(dpi=200, fit=contain, format=jpeg)` plus a new
  `max_pixels` in `plan()`. `dpi` and `max_pixels` come from the adapter.
- `max_pixels` 4,718,592 (`4608 * 32 * 32`, Qwen3-VL cookbook maximum for markdown) is about 4,600 image tokens; with
  8,192 output tokens a page fits a 16k context. A4 at 200 dpi is 3.9 MP and is not reduced.
- The PDF is reopened for each page. Measured on the host (warm cache, pool round trip excluded): 0.3 to 3.5 ms
  against 15 to 124 ms of render and encode; 83 ms with 500,000 extra objects, 658 ms with a broken xref on top.
  todo: render pages in batches if `Server-Timing` shows it matters.

### Scheduling

```
ocr not configured? 422                          before materialize
render page 1 -> page count > max_pages? 413     before any model call
each page:  OCR slot { render slot { render }, model call } -> delete the JPEG
join by page number, write the .md, chunk in the markdown pool
```

- `OcrService.read(page: Callable[[], Awaitable[Path]]) -> str` takes an OCR slot (`asyncio.Semaphore(concurrency)`),
  awaits `page()` once, then calls the adapter. Rendering inside the slot bounds the OCR renders in flight per
  container; outside it, 64 requests would queue hundreds of tasks in the FIFO render pool and starve `/v1/thumbnail`.
- `OcrService.rendering()` is a second semaphore of `max(1, processes // 2)` (`Topology`) around every OCR render,
  page 1 included, so slow PDFs cannot take the whole shared pool (except with `processes: 1`). Order is always OCR
  slot, then render slot.
- A request keeps at most `concurrency` pages queued: the semaphore is FIFO, and a long document must not put every
  other request behind all its pages.
- The slot is released while waiting for a retry; the JPEG is kept, so the page is not rendered again.
- The page loop lives in the `api/markdown.py` coordinator, in a `TaskGroup`: the first final error cancels the other
  pages, and the group is unwrapped to its `ProblemError`. Cancelled calls may still be billed upstream.
- Chunking runs in the markdown pool through a new worker function that takes the assembled markdown.

### Retries

- Transient failures, `429` and timeouts: 3 attempts in total, exponential backoff with jitter, `Retry-After` honored
  up to 30s; a longer one fails at once.
- Rejections and truncation are not retried: at temperature 0 a truncated page (usually a repetition loop) repeats.
- 120s per model call (`asyncio.timeout`); `limits.deadline` stays the ceiling and usually ends a hanging backend.

## Configuration

```yaml
ocr:                                  # absent: OCR is disabled
  type: qwen                          # qwen | mistral
  model: Qwen/Qwen3-VL-8B-Instruct    # required for qwen, mistral defaults to mistral-ocr-latest
  url: http://vllm:8000/v1            # required for qwen, optional for mistral
  api_key: ${OCR_API_KEY}             # optional for qwen, required for mistral
  concurrency: 8                      # pages in flight per container
  max_pages: 100                      # longest document, raise together with limits.deadline
  extra_body: {}                      # qwen only, merged verbatim into the request
```

- Discriminated union on `type`, `api_key` is a `SecretStr`. One backend per deployment: where a document may be sent
  is the operator's decision, never a request field. Two data perimeters are two deployments.
- `concurrency` is per container: replicas multiply it. No distributed rate limiter (stateless service).
- `warm()` builds the client and never calls the backend: an OCR outage must not stop startup or affect `/ready`.
- Startup log: `type`, `model`, `concurrency`, `max_pages` only.

## Errors

| Case                                                              | Code                                    |
|-------------------------------------------------------------------|-----------------------------------------|
| `readMode: ocr` without a configured backend, for every format    | `422 ocr_not_configured`                |
| More pages than `ocr.max_pages`                                   | `413 too_many_pages` (`TooManyPages(SourceTooLarge)`), page count in `detail` |
| Source image trips the bomb guard                                 | `413 image_too_large`                   |
| Backend fails after retries, rejects the request or answers garbage | `502 ocr_failed`                      |
| Output truncated                                                  | `502 ocr_failed`, page in `detail`      |
| Still rate limited after retries                                  | `503 ocr_rate_limited` with `Retry-After` |
| Page times out after retries                                      | `504 processing_timeout`                |

- All or nothing: one failed page fails the request. todo: `options.onPageError: skip`, returning the other pages.
- `detail` has the page number and the upstream status, never the upstream body or URL.

## Security

- `mistral` sends page images to a third party: stated in `config.example.yml` and `docs/security.mdx`.
- Page text can steer the model (prompt injection). The output is text only and must be treated as untrusted.
- Document content, model answers and keys are never logged.
- Hostile PDFs: the page side is clamped (4096 px, `max_pixels`), a render is killed at 60s, the deadline and the
  render slots bound the rest. Worker memory is not limited: an image inside a PDF can expand to gigabytes, as in
  `/v1/render` today (`AGENTS.md`, Future Issues).

## Validation

The spike was dropped by decision: live calls only check the shape of requests and responses (`live` tests).

- `qwen`: checked through OpenRouter with `qwen/qwen3-vl-8b-instruct`, pipe table included.
- `mistral`: never called, no key. Request, response and errors follow the SDK types over a fake transport.
  todo: confirm tables come inline as pipe tables, how truncation shows and the request limits.
- todo: seconds per page (10s assumed for `max_pages`), repetition loops and quality on scans, dense tables, receipts
  and handwriting.
- todo: hidden slides in the PDF export.
- Both SDKs work on Python 3.14: import in 0.3s and 0.2s, about 31 MB installed in total plus OpenTelemetry for
  `mistralai`.

## Tests

- Adapters: real SDKs over a fake httpx transport; cleaning, truncation, error mapping, no SDK retry, no environment
  key.
- Service: fake adapter; slots, render cap, attempts, slot released while waiting, cancellation.
- API: fake `OcrService`; fallback and effective mode, page limit before any call, page order, markers, chunks with
  `page`, `output=file`, destination, first frame only.
- `live` mark against real backends, deselected by default and in CI (`adr/development.md`).

## Delivery

Same change: `config.example.yml`, Bruno `Markdown.yml`, `docs/` (markdown, formats, chunking, configuration, errors,
security, sizing), README. Chart: no template change, key through `env`/`envFrom`; the opt-in NetworkPolicy blocks
private ranges, so an in-cluster backend needs a `networkPolicy.extraEgress` rule, documented in `values.yaml`.

## Out of Scope

- todo: whole document in one call for Mistral, to save requests on long PDFs.
- todo: `options.pages`, to split documents over `max_pages`.
- todo: token and cost reporting, with metrics.
- Layout blocks and bounding boxes, other backends (`dots`), local inference, per-request backend choice.
