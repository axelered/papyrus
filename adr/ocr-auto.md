# OCR Auto

`readMode: auto` on `POST /v1/markdown` picks `text` or `ocr` per document with a cheap deterministic classifier,
so scans get read and born-digital files keep the exact text. Extends `adr/ocr.md` (modes, OCR path, errors) and
`adr/markdown.md` (text path); changes the `readMode` default from `text` to `auto`.

## Request

| Option     | Type                         | Default |
|------------|------------------------------|---------|
| `readMode` | `text` \| `auto` \| `ocr`    | `auto`  |

- `options.readMode` in the response is the resolved mode, `text` or `ocr`, never `auto` (`adr/api.md`: options
  report how the document was processed). It names the engine: markitdown or the configured backend.
- The document class is logged at info with the request, not returned: classes and thresholds stay internal.
  todo: a response field (`source.contentClass`) if clients ask why a document was read with OCR.
- With a backend configured, documents of clients that never asked for OCR are now sent to it, a third party with
  `mistral`: stated in `config.example.yml` and the docs.

## Routing

The routing is the business logic of `api/markdown.py` and stays inline, one `if`/`elif` per mode; services only
report what they can read.

```python
document = await detect.detect(source.path)
# todo: for mode=OCR modernize can be skipped, office files are converted to PDF anyway
document = await convert.modernize(document, workspace)

mode = options.read_mode
if mode == ReadMode.OCR:
    if not ocr.configured:
        raise OcrNotConfigured(...)
    if not ocr.accepts(document.format):
        mode = ReadMode.TEXT  # fallback: formats without pages are read as text
elif mode == ReadMode.AUTO:
    if not ocr.configured or not ocr.accepts(document.format):
        mode = ReadMode.TEXT  # fallback: no backend, or a format without pages
    elif document.format.group in OCR_ONLY:
        mode = ReadMode.OCR  # images, presentations
    else:
        mode = ReadMode.OCR if await markdown.needs_ocr(document) else ReadMode.TEXT

if mode == ReadMode.OCR:
    try:
        result = await ocr.read(document, workspace, target, options.chunking, chunks_target)
    except TooManyPages:
        if options.read_mode != ReadMode.AUTO:
            raise
        mode = ReadMode.TEXT  # fallback: too long to read with OCR, auto reads it as text
if mode == ReadMode.TEXT:
    if not markdown.accepts(document.format):
        raise UnsupportedFormat(...)  # hint to readMode ocr for images
    result = await markdown.read(document, target, options.chunking, chunks_target)
```

- The `accepts` check sits with the text read, not in a `text` branch: every path ending in `text` (requested, or a
  fallback of `ocr` and `auto`) gets the same `415` and its hint to `readMode: ocr` for images.
- Stages: `modernize`, `classify`, `ocr` or `markdown` in `Server-Timing`.

- `OCR_ONLY` (`IMAGE`, `PRESENTATION` groups) is the one routing table of the route: images cannot be read as text,
  slide layout read as flat text carries little meaning.
- Per document, no splicing of text and OCR pages: markitdown output for PDFs has no page boundaries, and one
  document gets one kind of markdown (`chunks[].page` present or absent throughout). A native document with a few
  scanned pages is read whole with OCR. todo: per page routing, `classify_pdf_page` is the break point.
- A file the classifier cannot open (encrypted, corrupt, broken zip) does not need OCR, `markdown.read` then returns
  its usual `415`.
- Legacy office files read with OCR are converted twice (to OOXML, then to PDF); accepted, see the todo above.

### Services

| Service                    | Capability                                                                              |
|----------------------------|-----------------------------------------------------------------------------------------|
| `ConvertService.modernize` | `doc`, `rtf`, `odt` to `docx`; `ppt`, `odp` to `pptx`; `ods` to `xlsx`; others as is. Returns `Document(path, lookup(label))`, no second detection. Owns the former `CONVERSIONS` |
| `MarkdownService`          | `accepts(format)` (former `MARKDOWN_FORMATS`), `read(document, target, chunking, chunks_target) -> Extracted` (markitdown or `fence_code`, chunking fused in the same worker call), `needs_ocr(document) -> bool` (logs the class), `chunk` |
| `OcrService(render, convert, markdown)` | `configured`, `accepts(format)` (`pdf`, images, `docx`, `pptx`), `read(document, workspace, target, chunking, chunks_target) -> Extracted`: the whole OCR path of `adr/ocr.md` |

- `Extracted(title, page_count, width, height)` in `types/markdown.py`; the response `source` describes the detected
  original, filled with these facts.
- `server/ocr/classify.py` has `needs_ocr(path, label) -> tuple[bool, str]`: picks the classifier by label and maps
  the class with `OCR_CLASSES` (`graphic`, `garbage`, `scan`). `OCR_CLASSES` lives in `classify_pdf.py`, whose early
  stop needs it; DOCX classes are a subset. Unreadable files are classed `unreadable`. The markdown worker calls it
  (`adr/architecture.md`, Services).
- The classifier runs in the markdown pool, not in `DetectService`: pdfium is not thread-safe and a crash on a hostile
  PDF must kill a worker, not the API process.
- Coordinators receive the typed `Services`, not `Any`.

### Fallback

Falls back to `text` only on facts known before any model call; a backend failure is never hidden.

| Case                                | `auto`                       | `ocr`                              |
|-------------------------------------|------------------------------|------------------------------------|
| No backend configured               | `text`                       | `422 ocr_not_configured`           |
| More pages than `ocr.max_pages`     | `text`, logged               | `413 too_many_pages`               |
| Backend error, rate limit, timeout  | as `ocr`                     | `502`, `503`, `504` (`adr/ocr.md`) |
| Format without pages                | `text`                       | `text`                             |

- No fallback on backend failure: `auto` reaches OCR only when the text is unusable, and `output=file` and
  `destination` do not show the resolved mode, so a fallback would store an empty `200`.
- The page limit fallback keeps the default change non-breaking: a long native report with one full-page chart got
  its text before and still does. A scan over the limit returns empty markdown as in `text`; `readMode: ocr` gives
  the explicit `413`.
- `TooManyPages` is raised by `ocr.read` on the first render, before any model call, which covers PDF, converted
  office files and presentations with one rule.

## Classifiers

`server/ocr/classify_pdf.py` and `server/ocr/classify_docx.py` return only a class. Deterministic, existing
dependencies (pypdfium2, stdlib), thresholds as module constants.

- Run in the markdown pool through `needs_ocr` (Services above): pypdfium2 is imported only in workers, and the render
  pool stays free for thumbnails and OCR pages. `init_worker` imports pypdfium2 for warm start.

### PDF

`classify_pdf_page` classes one page from its pypdfium2 objects:

- `chars`: non-whitespace characters of the text layer (`get_textpage().get_text_bounded()`).
- `garbage`: share of `U+FFFD`, private-use (`Co`) and control (`Cc`) characters (`unicodedata`).
- `image_area`: image bounding boxes over the page area, summed and capped at 1; page-sized images count, that is
  what a scan looks like.
- `content_area`: same over images and paths; paths covering 90% of the page or more are backgrounds and excluded.
- `invisible`: share of text objects in render mode 3, the OCR layer a scanner puts behind its image.

| Class     | Condition, first match wins                                  | Catches                                    |
|-----------|--------------------------------------------------------------|--------------------------------------------|
| `empty`   | `chars < 32` and `content_area <= 10%`                       | blank pages, a lone rule or page number    |
| `graphic` | `chars < 32` and `content_area > 10%`                        | scans, outlined text, full-page figures    |
| `garbage` | `garbage > 10%`                                              | fonts without ToUnicode                    |
| `scan`    | `chars < 200` and `image_area > 80%` and `invisible > 50%`   | scans whose OCR layer recovered little     |
| `text`    | otherwise                                                    | native text, scans with a usable OCR layer |

- `classify_pdf` returns the first page in an OCR class and stops there, else `text` if any page is `text`, else
  `empty`. About 6 ms per page.
- The invisible text condition keeps photo covers with a visible title on `text`.
- Full-page figures in native reports route the whole document to OCR (87 of 704 `TEXT` labelled samples): correct
  output at a higher cost. todo: inspect the weakest hits, consider `CONTENT_COVERAGE` 0.2.
- Mojibake (wrong but valid characters) passes as `text`.
- todo: bounds of objects nested in form XObjects may be in form space, check against real samples.

### DOCX

DOCX text is the XML itself and always readable; OCR pays off only when images carry the content, a scan in Word.

- `chars`: alphanumeric characters of the `w:t` runs of `word/document.xml`, text boxes included, streamed with
  `iterparse` and stopped once the threshold is settled (about 1 ms on 5M character documents).
- `rasters`: raster entries under `word/media/` of any size; `emf` and `wmf` are vector drawings.
- `images`: rasters of at least 32 KB by declared zip entry size, nothing decompressed; smaller ones are logos.

| Class     | Condition                                   | Catches                                       |
|-----------|---------------------------------------------|-----------------------------------------------|
| `empty`   | `chars < 32` and no rasters                 | blank documents                               |
| `graphic` | `chars < 32` and any raster                 | scans pasted in Word, small ones included     |
| `graphic` | `images > 0` and `chars < 200 * images`     | scans with a typed caption, a captioned map   |
| `text`    | otherwise                                   | native documents, with or without screenshots |

- A photo cover or section background cannot send a report to OCR; a document mixing native and scanned pages is
  `text` and its scanned pages are not recovered. Per page image coverage was rejected: DOCX does not reliably record
  page boundaries, covers become false positives, and a full parse costs seconds on large documents.

## Tests

- Classifiers on documents generated in `test/fixtures/samples.py`, no committed binaries:
  - `test/ocr/test_classify_pdf.py`: one page per class from raw content streams (`samples.pdf_streams`: blank,
    full-page image, text, image behind invisible text, photo cover with visible text, control-character glyphs),
    early stop, `PdfiumError` on corrupt input.
  - `test/ocr/test_classify_docx.py`: `_zip` with random bytes of the declared size as images; each class, the 32 KB
    boundary, early stop, broken zip.
- `test/api/test_markdown_ocr.py` over the fake adapter: `auto` default, scan to `ocr` and native to `text` with the
  resolved mode, no backend to `text`, backend failure `502`, page limit to `text`, images and presentations to
  `ocr`, legacy `doc` modernized once and classified as `docx`, `ocr` on a spreadsheet falling back to `text`.
- Thresholds are tuned with `.nogit/detect.py` on `.nogit/dataset` (1,272 PDFs) and `.nogit/docx` (83 DOCX, labels in
  `labels.json`), outside the repository and CI.

## Delivery

Same change: `ReadMode.AUTO` and the default in `types/markdown.py` and the Python SDK (its schema test pins the
server enum); then the TypeScript SDK, Bruno `Markdown.yml`
(auto default, resolved mode, page limit fallback), `docs/` (markdown and OCR guides: routing, the page limit
fallback, documents sent to the backend by default), `config.example.yml` (`ocr` section and `max_pages` comment).

## Out of Scope

- Splicing text and OCR output page by page (todo above).
- Detecting mojibake; OCR of images inside native DOCX documents.
- Per request thresholds or classifier configuration.
