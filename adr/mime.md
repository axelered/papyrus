# MIME Detection

Application-side content type detection. Every materialized source becomes a typed `Document` before any service
runs, so coordinators can route on type (e.g. legacy formats through a conversion first), fail fast on unsupported
input, and report what was detected. Extends `adr/architecture.md`.

## Goals

- Content is the source of truth; client file names and `Content-Type` are hints only.
- One canonical format table drives detection, per-op allowlists, future routes and docs.
- Detected type travels with the result so downstream steps can use it. todo: context-aware markdown chunking keyed
  on `source_type` (per sheet for spreadsheets, per slide for presentations, per symbol for code by language).
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
@dataclass(frozen=True)
class Format:
    label: str        # magika label, doubles as language for code ('python', 'rust')
    mime_type: str
    extension: str
    group: str        # document, spreadsheet, presentation, email, archive, markup, data, code, text, unknown
    base: str | None  # parent label used by extension refinement
    derived: bool     # True when built from magika metadata instead of the explicit table


@dataclass(frozen=True)
class Document:
    path: Path        # in the workspace, extension normalized
    format: Format
```

- `DetectService.detect(path) -> Document` runs in the coordinator right after `materialize`.
- `materialize` keeps its signature; detection is an explicit coordinator step.
- Detection failures are `500 processing_failed`.

## Canonical Format Table

`server/formats.py`, two tiers.

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
| `ipynb`    | `application/x-ipynb+json`                                                  | `ipynb` | code         | `json` |
| `unknown`  | `application/octet-stream`                                                  | `bin`   | unknown      |        |

- Each format also lists the extensions that map to it (e.g. `htm`, `markdown`, `docm`) for the refinement rule.

### Derived Formats

- Every other magika label with `is_text` (134 text labels in total, 93 in group `code`, e.g. `python`,
  `typescript`, `rust`, `sql`, `dockerfile`, `yaml`, `toml`, `latex`, `rst`, `eml`, `srt`) is derived from magika
  metadata: its label, MIME (`text/x-python`), first extension (else `txt`), group, base `txt`, `derived=True`.
- A derived label whose magika group or MIME is wrong for our use gets an explicit entry instead.
- Non-text labels outside the explicit table map to `unknown`, keeping magika's label for logs and `detail`.
- A test pins magika's label for one fixture per format, so a magika upgrade that changes labels fails CI instead
  of silently rerouting.

## Resolution Rules

Applied in order; the file is never rejected for a mismatch (the virus scan is the safety net).

1. magika returns a label that is a **base** of the extension's format (directly or transitively): the extension
   refines it (`data.csv` + `txt` -> `csv`, `feed.rss` + `xml` -> `rss`, `book.docx` + `zip` -> `docx`).
2. magika returns `unknown`: use the extension's format, else `unknown`.
3. Otherwise magika wins (`report.pdf` + `html` -> `html`).

## Extension Normalization

- When the file's extension does not belong to the resolved format, rename it in the workspace to
  `<stem>.<format ext>`. The stem is kept, so output names do not change (`report.pdf` that is HTML gives `report.md`).
- Every tool (markitdown, LibreOffice import filters, pdfium) then sees a correct extension with no per-tool
  integration.

## Routing

- Each op declares an allowlist of format labels in its route module. Anything else gives
  `415 unsupported_format` with the detected MIME in `detail`, before a worker is used.
- todo: future routes are entries next to the allowlist, e.g. `doc`, `ppt`, `rtf`, `odt` -> convert service -> op.

## Exposure

- JSON results and destination receipts gain an additive `source_type: {mime_type, extension, label, group}` field;
  for code `label` is the language.
- File responses are unchanged.

## Out of Scope

- Header-based exposure for file responses, per-request type overrides, rejecting spoofed extensions.
