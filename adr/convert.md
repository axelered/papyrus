# Document Conversion

Internal conversion of legacy and OpenDocument formats with LibreOffice, so existing operations can accept them
(`doc` -> `docx` -> markdown, later office -> `pdf` -> thumbnail). Plugs into the Pool service kind of
`adr/architecture.md`; routing uses the format labels of `adr/mime.md`.

## Goals

- No soffice startup per conversion: one LibreOffice instance stays loaded per worker process.
- One Python interpreter (3.14 venv), no RPC, no ports, no daemon supervision code.
- Structure survives conversion: legacy text formats become OOXML, not PDF, so markitdown keeps headings and tables.
- Untrusted documents can neither run macros nor reach the network or the local filesystem through links.
- todo: public `POST /v1/convert` (Gotenberg-like, any supported format to `pdf` or OOXML) on top of this service.

## Decision

LibreOfficeKit (LOK), the in-process C API of LibreOffice used by Collabora Online, called through a small `ctypes`
binding inside a pebble pool worker.

| Option                            | Verdict                                                                       |
|-----------------------------------|-------------------------------------------------------------------------------|
| `soffice --convert-to` per call   | Rejected: 1-3s startup per conversion                                         |
| Raw pyuno over a socket           | Rejected: pyuno only loads in Debian's system Python, reimplements unoserver  |
| unoserver daemon                  | Fallback: second interpreter, cross-interpreter version pin, about 150 lines of supervision (spawn, readiness, process group kill, recycle, profile wipe) |
| Gotenberg sidecar                 | Rejected: second container and deployment unit                                |
| LOK in a pebble pool              | **Chosen**: pebble already gives timeout kill, crash isolation, recycling and lazy start |

- No maintained Python LOK binding exists (`pylokit` last released 2016, `pylibreoffice` abandoned after 4 releases
  in Dec 2024), so the binding is ours. It covers only the stable, append-only part of `LibreOfficeKit.h`.
- If the spike gate fails, switch to unoserver behind the same `ConvertService` interface.

## Stack

| Concern    | Choice                                                                                     |
|------------|--------------------------------------------------------------------------------------------|
| Engine     | LibreOffice from Debian trixie (`libreoffice-{writer,calc,impress}-nogui`) in the runtime image |
| Entry      | `/usr/lib/libreoffice/program/libmergedlo.so`, symbol `libreofficekit_hook_2(install, profile_url)` |
| Binding    | `server/services/lok.py`, `ctypes`, no new Python dependency                               |
| Fonts      | `fonts-liberation` (metric compatible with Office fonts, matters for PDF output)           |
| Base image | Stays `python:3.14-slim` (Debian); Alpine rejected: no musl wheels for onnxruntime (magika) |

## Binding

- Declares struct prefixes up to the last member used and checks `nSize` covers them, so newer LibreOffice versions
  with appended members keep working.
- Office: `documentLoadWithOptions`, `getError`, `freeError`. Document: `saveAs(url, format, filter_options)`,
  `destroy`. Nothing else is declared, unsafe members (`runMacro`) are unreachable from Python.
- `saveAs` picks the export filter from the format name (`docx`, `xlsx`, `pptx`, `pdf`).
- `lok_init` runs once per process and LOK is never destroyed: teardown is known to hang or crash, and pebble
  workers exit through `os._exit` (forkserver) without running LibreOffice destructors.
- LOK must run on the main thread of its process; pebble runs tasks on the worker main thread.

## Service

- `ConvertService` (pool kind), worker `convert_document(source, target, format, profiles)`, one `Office` per worker
  created on first task.
- Interface: `await convert.convert(source, target, ConvertFormat)`; `ConvertFormat` (`server/types/convert.py`) is
  `DOCX`, `XLSX`, `PPTX`, `PDF`.
- Sizing heuristics: `processes = max(1, workers.processes // 2)` (LibreOffice RAM), timeout 120s, `max_tasks` 50
  (recycles the instance against leaks, LOK init takes about 0.3s).
- Profiles: each worker uses `<limits.tmp_dir>/lo-profiles/<pid>`; a starting worker removes directories of dead
  pids, `close()` removes the root. Profiles are never shared (LibreOffice locks them).
- The profile `user/registrymodifications.xcu` disables document lock files (`UseDocumentOOoLockFile`,
  `UseDocumentSystemFileLocking`): a worker killed mid-load otherwise leaves `.~lock.<name>#` next to the source and
  every later load of that file fails.
- `XDG_CACHE_HOME` points into the profile: dconf and fontconfig need a writable cache and the runtime user has no
  home.

### Routing

| Detected label        | Converted to | Then        |
|-----------------------|--------------|-------------|
| `doc`, `rtf`, `odt`   | `docx`       | markdown    |
| `ppt`, `odp`          | `pptx`       | markdown    |
| `ods`                 | `xlsx`       | markdown    |
| `xls`                 | none         | markitdown reads it natively |

- The markdown route converts by format label before the markdown worker (`CONVERSIONS` in `api/markdown.py`);
  the converted file is a new workspace file and `source_type` still reports the original format.
- `ConvertService` is registered in `Services` (`factory.py`) with profiles in `<limits.tmp_dir>/lo-profiles`; the
  pool starts on the first legacy request, so deployments that never convert pay no RAM.
- Without LibreOffice (host development) legacy requests fail with `500 processing_failed`; API tests replace the
  service with a fake writing OOXML samples, end to end tests carry the `full` mark and run only in the image
  (`adr/development.md`).
- todo: office -> `pdf` for the thumbnail service.

## Security

LOK enforces these on every load, no profile registry hardening needed:

- `MacroExecutionMode=NEVER_EXECUTE` and `DisableMacrosExecution` unless `EnableMacrosExecution=true` is passed
  (never).
- `Batch=true` load option: dialogs are cancelled silently, so password protected files fail instead of hanging.

Set in the worker environment before `lok_init`:

- `LOK_HOST_ALLOWLIST=invalid`: remote hosts must fully match the regex, the reserved `invalid` name matches nothing
  real, so linked remote resources are never fetched (would bypass the SSRF guard of `io/http.py`).
- `LOK_ALLOWED_EXTREF_PATHS=` (empty): blocks all `file://` external references, so a document cannot pull local
  files (`/etc/...`, other workspaces) into its output. Defense in depth: linked Writer sections were not updated on
  load even without it, so no positive control exists yet.
- The remote guard is proven: without `LOK_HOST_ALLOWLIST` a linked image is fetched during both `pdf` and `docx`
  export, with it nothing is requested.

## Errors

| Case                                                  | Error                        |
|-------------------------------------------------------|------------------------------|
| Document cannot be loaded (corrupt, truncated, encrypted) | `415 unsupported_format` |
| Document type has no export filter for the target     | `415 unsupported_format`     |
| LibreOffice missing or fails to initialize            | `500 processing_failed`      |
| Worker crash (segfault on a hostile document)         | `500 processing_failed`      |
| Exceeds 120s                                          | `504 processing_timeout`     |

- Bytes no import filter recognizes are not rejected: LibreOffice falls back to its text filter and converts them as
  plain text. Routing only sends magika-detected formats, so this cannot be reached through the API.

## Spike Gate

Passed. `test/services/test_convert.py` covers it as `full` tests, run in the image (no LibreOffice on the host):

1. `ctypes` loads LOK from Debian trixie and converts `doc`, `ppt`, `xls`, `rtf`, `odt`; OOXML keeps `Heading1`.
   Legacy fixtures are produced by the engine itself, encrypted ones through the `,Password=...PASSWORDEND` save
   option.
2. Truncated and encrypted input fails with `415`, encrypted without hanging.
3. Recycling (`max_tasks=1`) re-initializes LibreOffice in a fresh worker without hanging.
4. A timed out conversion kills the worker, the next worker converts the same file and the stale profile is pruned.
5. Remote linked images are not fetched; `file://` sections are not embedded.

Run with the dekit `test` task locally; CI runs them on every change (`adr/development.md`).

## Resources

- Image grows by about 630 MB (1.52 GB total).
- Measured per worker: about 200 MB RSS after init, about 330 MB after warm conversions, flat over 40 conversions.
  Large spreadsheets or decks can spike past 1 GB.
- Measured latency: about 0.2s per small document warm, about 0.9s for the first; serial per worker, scale with
  workers and replicas.

## Out of Scope

- OCR, printing, document comparison, password supply for encrypted input.
- todo: deny network egress for the whole worker (network namespace or deployment policy) as defense in depth.
