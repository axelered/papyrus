# MIME Detection

Application-side content type detection. Every materialized source becomes a typed `Document` before any service
runs, so coordinators can route on type (e.g. legacy formats through a conversion first), fail fast on unsupported
input, and report what was detected. Extends `adr/architecture.md`.

## Goals

- Content is the source of truth across text and binary; within text formats the file name decides (rule 2), since
  magika confuses small text formats with overlapping syntax.
- One canonical format table drives detection, per-op allowlists, future routes and docs.
- Detected type travels with the result so downstream steps can use it; chunking (`adr/chunking.md`) uses it to
  pick code languages and slide metadata.
- Source code and other text formats common in RAG corpora are first-class, with the language preserved.

## Stack

| Concern   | Choice                                                                                |
|-----------|---------------------------------------------------------------------------------------|
| Detector  | magika (already installed through markitdown, same engine, so both always agree)      |
| Placement | `DetectService`, inline service kind: one lazy `Magika()` in the API process, `asyncio.to_thread` (onnxruntime releases the GIL, `run` is thread-safe) |
| Cost      | About 100 MB RSS and about 1 s on first use in the API process, about 5 ms per file    |

Python `mimetypes` is never used for policy: on Windows it reads the registry, so results differ between dev and
Docker.

## Document

```python
class FormatGroup(StrEnum):
    DOCUMENT = 'document'
    SPREADSHEET = 'spreadsheet'
    PRESENTATION = 'presentation'
    EMAIL = 'email'
    ARCHIVE = 'archive'
    MARKUP = 'markup'
    DATA = 'data'
    CODE = 'code'
    TEXT = 'text'
    IMAGE = 'image'
    UNKNOWN = 'unknown'


class FormatLabel(StrEnum):  # explicit table only, e.g. PDF = 'pdf', DOCX = 'docx', IPYNB = 'ipynb'
    ...


@dataclass(frozen=True)
class Format:
    label: str                  # FormatLabel value, or magika ContentTypeLabel value for derived formats
    mime_type: str
    extension: str
    group: FormatGroup
    base: FormatLabel | None    # parent used by extension refinement
    text: bool                  # decodable text; derived text formats are processable by text-family allowlists
    derived: bool               # True when built from magika metadata instead of the explicit table
    aliases: tuple[str, ...]    # extra extensions mapping to this format (htm, docm)


@dataclass(frozen=True)
class Document:
    path: Path        # in the workspace, extension normalized
    format: Format
```

- `DetectService.detect(path) -> Document` runs in the coordinator right after `materialize`.
- `materialize` keeps its signature; detection is an explicit coordinator step.
- Detection failures are `500 processing_failed`.

## Canonical Format Table

`server/services/detect.py` (the public `FormatGroup` and `FormatLabel` enums live in `server/types/common.py`), two
tiers. Explicit formats are reachable as constants (`Formats.PDF.mime_type`), so code never
writes a MIME or label literal. Derived formats are looked up internally by magika's `ContentTypeLabel`; their label
and MIME are open sets and stay `str`.

### Explicit Formats

Keyed by magika label; MIME, extension and group are ours, not magika's (magika maps `ipynb` and `json` to the same
MIME, gives `outlook` no extension, and groups `csv` as code). Formats magika cannot tell apart from their base
(`rss`, `atom`) are reachable only by extension refinement.

| Label      | MIME                                                                        | Ext     | Group        | Base   |
|------------|-----------------------------------------------------------------------------|---------|--------------|--------|
| `pdf`      | `application/pdf`                                                           | `pdf`   | document     |        |
| `docx`     | `application/vnd.openxmlformats-officedocument.wordprocessingml.document`   | `docx`  | document     | `zip`  |
| `pptx`     | `application/vnd.openxmlformats-officedocument.presentationml.presentation` | `pptx`  | presentation | `zip`  |
| `xlsx`     | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`         | `xlsx`  | spreadsheet  | `zip`  |
| `epub`     | `application/epub+zip`                                                      | `epub`  | document     | `zip`  |
| `zip`      | `application/zip`                                                           | `zip`   | archive      |        |
| `doc`      | `application/msword`                                                        | `doc`   | document     |        |
| `ppt`      | `application/vnd.ms-powerpoint`                                             | `ppt`   | presentation |        |
| `xls`      | `application/vnd.ms-excel`                                                  | `xls`   | spreadsheet  |        |
| `outlook`  | `application/vnd.ms-outlook`                                                | `msg`   | email        |        |
| `rtf`      | `text/rtf`                                                                  | `rtf`   | document     | `txt`  |
| `odt`      | `application/vnd.oasis.opendocument.text`                                   | `odt`   | document     | `zip`  |
| `ods`      | `application/vnd.oasis.opendocument.spreadsheet`                            | `ods`   | spreadsheet  | `zip`  |
| `odp`      | `application/vnd.oasis.opendocument.presentation`                           | `odp`   | presentation | `zip`  |
| `txt`      | `text/plain`                                                                | `txt`   | text         |        |
| `markdown` | `text/markdown`                                                             | `md`    | markup       | `txt`  |
| `csv`      | `text/csv`                                                                  | `csv`   | data         | `txt`  |
| `tsv`      | `text/tab-separated-values`                                                 | `tsv`   | data         | `txt`  |
| `html`     | `text/html`                                                                 | `html`  | markup       | `txt`  |
| `xml`      | `application/xml`                                                           | `xml`   | markup       | `txt`  |
| `rss`      | `application/rss+xml`                                                       | `rss`   | markup       | `xml`  |
| `atom`     | `application/atom+xml`                                                      | `atom`  | markup       | `xml`  |
| `json`     | `application/json`                                                          | `json`  | data         | `txt`  |
| `jsonl`    | `application/jsonl`                                                         | `jsonl` | data         | `json` |
| `ipynb`    | `application/x-ipynb+json`                                                  | `ipynb` | code         | `json` |
| `png`      | `image/png`                                                                 | `png`   | image        |        |
| `jpeg`     | `image/jpeg`                                                                | `jpg`   | image        |        |
| `webp`     | `image/webp`                                                                | `webp`  | image        |        |
| `gif`      | `image/gif`                                                                 | `gif`   | image        |        |
| `bmp`      | `image/bmp`                                                                 | `bmp`   | image        |        |
| `tiff`     | `image/tiff`                                                                | `tiff`  | image        |        |
| `avif`     | `image/avif`                                                                | `avif`  | image        |        |
| `unknown`  | `application/octet-stream`                                                  | `bin`   | unknown      |        |

- Each format also lists the extensions that map to it (e.g. `htm`, `markdown`, `docm`, `jpeg`, `tif`) for the
  refinement rule.
- Image formats are explicit so the render allowlist (`adr/render.md`) is a `frozenset[FormatLabel]`. Markdown still
  rejects them (not text); only the `source` type reported in its `415` detail changes from `unknown` to `image`.

### Derived Formats

- Every other magika label with `is_text` (134 text labels in total, 93 in group `code`, e.g. `python`,
  `typescript`, `rust`, `sql`, `dockerfile`, `yaml`, `toml`, `latex`, `rst`, `eml`, `srt`) is derived from magika
  metadata: its label, MIME (`text/x-python`), first extension (else `txt`), group, base `txt`, `derived=True`.
- Derived group: magika `code` -> `code`, any other text group -> `text`.
- A derived label whose magika group or MIME is wrong for our use gets an explicit entry instead.
- Non-text labels outside the explicit table are derived too (magika label and MIME, e.g. `svg` / `image/svg+xml`) with
  group `unknown` and `text=False`, so responses stay informative while text-family allowlists reject them.
- magika metadata comes from its bundled content types knowledge base (`Magika._load_content_types_kb`, JSON only,
  no model load). It is private API, so tests pin that every explicit label exists in it.
- Extension index: derived text formats by magika extensions, then explicit formats override.
- A test pins magika's label for one fixture per format, so a magika upgrade that changes labels fails CI instead
  of silently rerouting. Fixtures are generated in `test/fixtures/samples.py` (pdf, docx, xlsx, pptx, zip, html,
  csv, json, jsonl, markdown, python, typescript, png, jpeg, webp, gif, bmp, tiff, avif). Legacy binaries (doc, ppt, xls, odp, ods) are produced by
  LibreOffice in `test/fixtures/legacy.py` and their labels are pinned inside the image
  (`test/api/test_markdown_convert.py`). todo: pin `msg` once a real sample file is checked in.

## Resolution Rules

Applied in order; the file is never rejected for a mismatch. magika's no-signal
labels (`empty`, `undefined`) count as `unknown`; a non-ok magika status (e.g. unreadable file) is a detection
failure, never a silent `unknown`.

- magika reports every ISO BMFF file (AVIF, HEIC, MP4) as `mp4`; when the `ftyp` major brand is `avif` or `avis`
  the content resolves to `avif` before the rules below, so a renamed AVIF is still detected by content.
- magika reports single-line JSON as `jsonl` (a valid JSONL document), so a minified `data.json` resolves to `jsonl`;
  both are processed as text, only the reported `source.label` differs.

1. magika returns a label that is a **base** of the extension's format (directly or transitively): the extension
   refines it (`data.csv` + `txt` -> `csv`, `feed.rss` + `xml` -> `rss`, `book.docx` + `zip` -> `docx`).
2. Content and extension are **both text** formats and the extension is not a generic ancestor of the content:
   the extension wins (`p.html` + `erb` -> `html`, `app.py` + `javascript` -> `python`), while a generic name keeps
   specific content (`notes.txt` + `python` -> `python`). magika separates text from binary reliably but confuses
   small text formats with overlapping syntax.
3. magika returns `unknown`: use the extension's format, else `unknown`.
4. Otherwise magika wins (`report.pdf` + `html` -> `html`, `photo.html` + `png` -> `png`).

## Extension Normalization

- When the file's extension does not belong to the resolved format, rename it in the workspace to
  `<stem>.<format ext>`. The stem is kept, so output names do not change (`report.pdf` that is HTML gives `report.md`).
- Every tool (markitdown, LibreOffice import filters, pdfium) then sees a correct extension with no per-tool
  integration.

## Routing

- Each op declares an allowlist (`frozenset[FormatLabel]`, plus group rules such as "every derived text format")
  in its route module. Anything else gives
  `415 unsupported_format` with the detected MIME in `detail`, before a worker is used.
- Routes through another service are entries next to the allowlist, e.g. `CONVERSIONS` in `api/markdown.py`
  (`doc` -> convert service -> markdown, `adr/convert.md`).

## Exposure

- The detected type is reported in `source` (`mimeType`, `extension`, `label`, `group`, see `adr/api.md`); for code
  `label` is the language.
- File responses carry no type metadata.

## Out of Scope

- Header-based exposure for file responses, per-request type overrides, rejecting spoofed extensions.
