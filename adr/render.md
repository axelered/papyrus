# Page Rendering

Rasterize one page of a document or image: `POST /v1/thumbnail` for fixed-size tiles in grids and lists,
`POST /v1/render` for readable pages in a viewer. Both share one coordinator and one pool service. Plugs into the
Pool service kind of `adr/architecture.md`, routes on the format labels of `adr/mime.md` and reaches office and HTML
input through `adr/convert.md`; HTML thumbnails are browser screenshots (`adr/browser.md`).

## Goals

- One page in, one image out: one `result` in the envelope of `adr/api.md`.
- Render directly at the final resolution (pdfium scale from page size), never render oversized and downscale.
- Thumbnails stay cheap for large office files: only the requested page is exported to PDF.
- Bounded worker memory: a pixel cap per side and the Pillow decompression bomb guard.
- todo: multi-page output (destination prefix with `{stem}-{n}.{ext}`) once a consumer needs it.

## Stack

| Concern   | Choice                                                                                  |
|-----------|-----------------------------------------------------------------------------------------|
| PDF       | pypdfium2 (wheel bundles pdfium, no apt packages); PyMuPDF rejected: AGPL                |
| Images    | Pillow                                                                                  |
| Office, HTML | `ConvertService` to `pdf` with the `PageRange` export option, then pdfium (LibreOffice already in the image) |
| Service   | `RenderService`, pool kind, processes = `workers.processes`, timeout 60s, `max_tasks` 200 |

- pypdfium2 and Pillow are explicit main dependencies (already locked through markitdown), imported only inside
  the worker. Pillow's wheel decodes webp, avif and tiff natively.

## Routes

Both in `server/api/render.py` (shared allowlist and coordinator). Default `output` is `file`.

### `POST /v1/thumbnail`

| Option   | Type                  | Default   | Rules                                   |
|----------|-----------------------|-----------|-----------------------------------------|
| `page`   | int                   | `1`       | `>= 1`                                  |
| `width`  | int                   | `256`     | `1..4096`                               |
| `height` | int                   | `256`     | `1..4096`                               |
| `fit`    | `contain` \| `cover`  | `contain` | `cover` scales to fill the box and center crops |
| `format` | `png` \| `jpeg` \| `webp` | `webp` |                                         |

### `POST /v1/render`

| Option   | Type                  | Default            | Rules                                     |
|----------|-----------------------|--------------------|-------------------------------------------|
| `page`   | int                   | `1`                | `>= 1`                                    |
| `width`  | int                   | none               | `1..4096`                                 |
| `height` | int                   | none               | `1..4096`                                 |
| `dpi`    | int                   | `150` when no size | `1..600`, mutually exclusive with `width`/`height` (`422`) |
| `format` | `png` \| `jpeg` \| `webp` | `png`          |                                           |

- `width` or `height` alone: the other side follows the aspect ratio. Both: contain within the box.
- A `dpi` render larger than 4096 px on a side is clamped to 4096 (keeping aspect), not rejected: the client cannot
  know the page size in advance.
- Raster images ignore `dpi` and render at natural size: a pixel image has no physical size and embedded DPI metadata
  is unreliable. The default request therefore works for every input; `width`/`height` still apply.

### Sizing Rules

- PDF pages are vectors: scale = `dpi / 72`, `width / page_width_pt`, contain `min(w / pw, h / ph)`, cover
  `max(w / pw, h / ph)` then center crop. Page rotation is applied by pdfium. pdfium sizes its bitmap with a plain
  `ceil` (A4 at 150 dpi is 1240x1755), so the scale is reduced by 1e-9 to keep exact sizes (800, 4096) exact.
- Raster images are never upscaled: a box larger than the image yields the natural size (cover still crops to the
  box aspect).
- Every rendered bitmap is clamped to 4096 px per side (aspect kept), including images at natural size and the
  intermediate bitmap of `cover`; for pathological aspects (a 1:100 page) the crop then ends up smaller than the box
  instead of allocating an unbounded bitmap.
- Encoding quality is fixed in code (jpeg and webp about 85), no option. todo: `quality` option on demand.
- PDF pages render on white with annotations and forms drawn. Images keep alpha for `png` and `webp`, are flattened
  on white for `jpeg`.

## Contract

`server/types/render.py`: `ThumbnailRequest`, `RenderRequest`, their options models, `Fit` (`contain`, `cover`),
`ThumbnailResponse`, `RenderResponse`. `ImageFormat` (`png`, `jpeg`, `webp` with MIME and extension) and `MAX_IMAGE_SIDE` live in
`server/types/common.py`, shared with `adr/browser.md`.

### File output

- Raw bytes with the image `Content-Type`; filename `{stem}.{ext}` for thumbnails, `{stem}-{page}.{ext}` for render.

### JSON output

The envelope of `adr/api.md`: `result` carries the image `width`, `height` and `base64` content; `source.pageCount`
and, for image input, `source.width`/`height`; `options` echoes the resolved options (`dpi` 150 when no size is set).

- `source.pageCount` is omitted for office and HTML input (only the requested page is exported, see Routing).

## Routing

| Detected label                                                     | Path                                   |
|--------------------------------------------------------------------|----------------------------------------|
| `pdf`                                                              | pdfium                                 |
| `png`, `jpeg`, `webp`, `gif`, `bmp`, `tiff`, `avif`                | Pillow                                 |
| `docx`, `pptx`, `xlsx`, `doc`, `ppt`, `xls`, `odt`, `odp`, `ods`, `rtf` | `convert.convert(source, pdf, ConvertFormat.PDF, page=<page>)`, then pdfium page 1 |
| `html` in `/v1/render`                                             | as office, through LibreOffice         |
| `html` in `/v1/thumbnail`                                          | browser screenshot of a box shaped viewport to a workspace PNG, then the render worker as an image |

- Allowlist is a `frozenset[FormatLabel]` plus a `PDF_CONVERSIONS` set in `api/render.py`; anything else is
  `415 unsupported_format` before a worker runs. Text, markdown, code and other derived formats are rejected.
- The coordinator builds an internal `RenderSpec` from either route's options and calls
  `await render.page(path, target, spec) -> Raster`; one worker `render_page` serves both routes.
- Multi-frame images (`tiff`, `gif`, animated `webp`): `page` selects the frame (`Image.seek`), so `page` means the
  same for every input.
- Spreadsheets render as LibreOffice paginates them (wide sheets split across pages); accepted quality caveat.
- `/v1/render` HTML goes through LibreOffice: no JS, basic CSS, remote images never load (`LOK_HOST_ALLOWLIST`, by
  design). todo: replace with an HTML > PDF > image pipeline on Chromium.

### HTML Thumbnails

Users expect a thumbnail of a web page to look like the page on screen, so `/v1/thumbnail` screenshots HTML instead of
paginating it. The coordinator composes the services (the render pool cannot reach the API process browser):

1. `page > 1` gives `422 page_out_of_range` before the browser runs: HTML has exactly one page.
2. `await browser.screenshot(origin, png, thumbnail_viewport(spec), document=path)`: the materialized HTML answers the
   main navigation of `origin` (see `adr/browser.md`, Documents). The viewport is 1280 wide (desktop layout) and
   `1280 * box_height / box_width` tall, clamped to 400..2160 (the screenshot height limit), so the capture has the box
   shape: a square box shows 1280x1280 of the page top, `contain` fills it and `cover` crops nothing unless the clamp
   applies. Scale 1, `load`,
   PNG (lossless, encoded once more by the render worker).
3. The PNG goes through the render worker like any image: box, `fit`, no upscaling, format. `pageCount` is `1`.

- `origin` is the source URL for http(s) sources, so a URL serving HTML yields a site preview with JS and relative
  assets; uploads and connector files get `http://document.invalid/<name>`.
- The same HTML thus looks like the screen in `/v1/thumbnail` and like LibreOffice print pages in `/v1/render`, until
  the render route moves to Chromium.
- `waitUntil` stays `load`; no new thumbnail options. todo: a `viewport` option if mobile previews are needed.

### Execution

All decoding, rasterizing, resizing and encoding run in the `RenderService` pool worker; the API process only
materializes, detects, routes, builds the spec and responds. Office and HTML input first passes through the convert
pool, which writes a one-page PDF into the workspace.

- Pool, not threads or inline: pypdfium2 serializes all calls behind one global lock, the work is CPU bound, hostile
  files can crash pdfium or image codecs (one worker dies, `500`), and pebble bounds time (60s) and leaks
  (`max_tasks`). Images take the same path for the same isolation.
- Only the worker opens the document, so the spec carries intent and the worker computes the scale (PDF) or target
  size (image) with one pure sizing function shared by both routes:

```python
@dataclass(frozen=True)
class RenderSpec:              # services/render.py, internal
    page: int
    width: int | None          # box, or a single side
    height: int | None
    dpi: int | None            # render route and OCR, ignored for raster images
    fit: Fit                   # thumbnail: contain | cover; render: contain
    format: ImageFormat
    max_pixels: int | None = None  # OCR only (adr/ocr.md): caps the area, aspect kept


@dataclass(frozen=True)
class Raster:                  # worker result; the coordinator maps it onto source and result (adr/api.md)
    width: int
    height: int
    page: int
    page_count: int | None     # None after a single-page office or HTML export
```

### Image Handling

- `ImageOps.exif_transpose` before sizing (phone photos).
- CMYK, palette and other modes are converted to RGB or RGBA before encoding.
- JPEG decoding uses `draft()` to decode near the target size; downscaling uses `LANCZOS`, cover uses `ImageOps.fit`.
- Pillow `MAX_IMAGE_PIXELS` stays enabled; the bomb warning is promoted to an error.
- `avif` is decoded by Pillow's bundled libavif; it is an input format only (see Out of Scope).

## Errors

| Case                                                        | Error                       |
|-------------------------------------------------------------|-----------------------------|
| Format not in the allowlist                                 | `415 unsupported_format`    |
| Corrupt or encrypted PDF, unreadable image                  | `415 unsupported_format`    |
| Image trips the decompression bomb guard                    | `413 image_too_large` (`ImageTooLarge(SourceTooLarge)`), pixel size in `detail` |
| `page` beyond the last page or frame                        | `422 page_out_of_range`, page count in `detail` for PDF, images and HTML thumbnails, none for office and render HTML |
| HTML thumbnail browser failures                             | as `/v1/screenshot` (`adr/browser.md`, Errors) |
| `dpi` combined with `width` or `height`, size out of bounds | `422 validation_error`      |
| Exceeds 60s                                                 | `504 processing_timeout`    |

## Spike

Passed, covered by `full` tests in `test/services/test_convert.py` and `test/api/test_render_convert.py`:

1. `PageRange=<n>` exports exactly that page for writer (docx), impress (pptx), calc (xlsx) and HTML documents.
2. A `PageRange` past the last page fails the save without reporting the page count. A save failure with `page > 1`
   maps to `422 page_out_of_range` (no count in `detail`); at `page == 1` it stays `415 unsupported_format`, since
   every LibreOffice document exports its first page.
3. HTML loaded through Writer paginates into A4 pages; no import filter override is needed.

## Tests

- Fixtures generated in `test/fixtures/samples.py`: multi-page PDF, rotated page, png with alpha, CMYK jpeg, jpeg
  with EXIF orientation, multi-frame tiff, avif, oversized image header for the bomb guard.
- Covered: the pure sizing function for every option combination (`contain`, `cover`, no upscaling, `dpi` ignored
  for images, 4096 clamp), frame selection, out of range, JSON and file outputs.
- Office and HTML end to end tests carry the `full` mark; API tests replace `ConvertService` with a fake writing a
  sample PDF.
- `mime` label pins gain one fixture per image format.

## Out of Scope

- Multi-page output, `quality` option, `heic` input (needs `pillow-heif`), `svg` (own rasterizer and external
  reference surface), text and code rendering.
- `avif` output: `ImageFormat` is shared with `/v1/screenshot`, whose CDP capture cannot encode it, and encoding is
  slow for large images.
- Website screenshots and JS-capable HTML: `adr/browser.md`; HTML thumbnails use it (see HTML Thumbnails).
