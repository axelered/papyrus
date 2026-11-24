# License

Papyrus is licensed under the Apache License 2.0 (`Apache-2.0`).

| Item            | Value                                 |
|-----------------|---------------------------------------|
| Commercial name | Papyrus                               |
| License         | Apache-2.0                            |
| Copyright       | Axelered D.o.o., Axelered LLC-Fz      |

## SBOM

Licenses as SPDX expressions, derived from the locked package metadata (`uv.lock`, installed `dist-info`) and upstream for image components. Versions are the locked ones at the time of writing; `uv.lock` is the source of truth.

### Direct runtime

| Package            | Version  | License                    | Commercial      |
|--------------------|----------|----------------------------|-----------------|
| charset-normalizer | 3.5.2    | MIT                        | Yes             |
| chonkie            | 1.7.0    | MIT                        | Yes             |
| fastapi            | 0.163.1  | MIT                        | Yes             |
| httpx              | 0.28.1   | BSD-3-Clause               | Yes             |
| magika             | 1.0.3    | Apache-2.0                 | Yes             |
| markitdown         | 0.1.9    | MIT                        | Yes             |
| pebble             | 5.2.3    | LGPL-3.0                   | Yes, unmodified |
| pillow             | 13.0.0   | MIT-CMU                    | Yes             |
| playwright         | 1.65.2   | Apache-2.0                 | Yes             |
| pydantic           | 2.14.3   | MIT                        | Yes             |
| pydantic-settings  | 2.16.3   | MIT                        | Yes             |
| pypdfium2          | 5.15.2   | Apache-2.0 OR BSD-3-Clause | Yes             |
| python-multipart   | 0.0.34   | Apache-2.0                 | Yes             |
| pyyaml             | 6.0.3    | MIT                        | Yes             |
| s3fs               | 2026.9.0 | BSD-3-Clause               | Yes             |
| tiktoken           | 0.14.0   | MIT                        | Yes             |
| uvicorn            | 0.54.0   | BSD-3-Clause               | Yes             |

### Development

| Package                 | Version | License                         | Commercial       |
|-------------------------|---------|---------------------------------|------------------|
| fastapi[standard]       | 0.163.1 | MIT                             | Yes              |
| openapi-spec-validator  | 0.9.0   | Apache-2.0                      | Yes              |
| pytest                  | 9.2.0   | MIT                             | Yes              |
| ruff                    | 0.17.5  | MIT                             | Yes              |
| uv                      | 0.11.30 | MIT OR Apache-2.0               | Yes              |

### Container image

| Component                                   | Source                 | License                                           | Commercial       |
|---------------------------------------------|------------------------|---------------------------------------------------|------------------|
| Python 3.14 (`python:3.14-slim`)            | Docker official image  | PSF-2.0                                           | Yes              |
| Debian base system                          | `python:3.14-slim`     | Mixed, per package (`/usr/share/doc/*/copyright`) | Yes, per package |
| LibreOffice writer, calc, impress (nogui)   | apt                    | MPL-2.0                                           | Yes, unmodified  |
| fonts-liberation                            | apt                    | OFL-1.1                                           | Yes              |
| tini                                        | apt                    | MIT                                               | Yes              |
| chrome-headless-shell (Chromium)            | `playwright install`   | BSD-3-Clause, plus bundled third-party licenses   | Yes              |
| Playwright driver (Node.js bundled)         | `playwright` wheel     | Apache-2.0, Node.js MIT                           | Yes              |
| tree-sitter grammars                        | `scripts.prefetch`     | Mostly MIT, per grammar                           | Yes              |

### Transitive runtime

| Package                   | Version     | License                                            | Commercial       |
|---------------------------|-------------|----------------------------------------------------|------------------|
| aiobotocore               | 3.10.0      | Apache-2.0                                         | Yes              |
| aiohappyeyeballs          | 2.7.1       | PSF-2.0                                            | Yes              |
| aiohttp                   | 3.14.5      | Apache-2.0 AND MIT                                 | Yes              |
| aioitertools              | 0.13.0      | MIT                                                | Yes              |
| aiosignal                 | 1.4.0       | Apache-2.0                                         | Yes              |
| annotated-doc             | 0.0.6       | MIT                                                | Yes              |
| annotated-types           | 0.8.0       | MIT                                                | Yes              |
| anyio                     | 4.16.0      | MIT                                                | Yes              |
| attrs                     | 26.1.0      | MIT                                                | Yes              |
| beautifulsoup4            | 4.15.0      | MIT                                                | Yes              |
| botocore                  | 1.43.117    | Apache-2.0                                         | Yes              |
| certifi                   | 2026.10.23  | MPL-2.0                                            | Yes, unmodified  |
| cffi                      | 2.1.1       | MIT-0                                              | Yes              |
| chonkie-core              | 0.10.2      | MIT OR Apache-2.0                                  | Yes              |
| click                     | 8.5.1       | BSD-3-Clause                                       | Yes              |
| cobble                    | 0.1.4       | BSD-2-Clause                                       | Yes              |
| colorama                  | 0.4.6       | BSD-3-Clause                                       | Yes              |
| cryptography              | 50.0.2      | Apache-2.0 OR BSD-3-Clause                         | Yes              |
| defusedxml                | 0.7.1       | PSF-2.0                                            | Yes              |
| et-xmlfile                | 2.0.0       | MIT                                                | Yes              |
| flatbuffers               | 25.12.19    | Apache-2.0                                         | Yes              |
| frozenlist                | 1.8.0       | Apache-2.0                                         | Yes              |
| fsspec                    | 2026.9.0    | BSD-3-Clause                                       | Yes              |
| greenlet                  | 3.5.6       | MIT AND PSF-2.0                                    | Yes              |
| h11                       | 0.16.0      | MIT                                                | Yes              |
| httpcore                  | 1.0.9       | BSD-3-Clause                                       | Yes              |
| idna                      | 3.20        | BSD-3-Clause                                       | Yes              |
| jmespath                  | 1.1.0       | MIT                                                | Yes              |
| lxml                      | 6.1.3       | BSD-3-Clause                                       | Yes              |
| mammoth                   | 1.13.0      | BSD-2-Clause                                       | Yes              |
| markdownify               | 1.2.3       | MIT                                                | Yes              |
| multidict                 | 7.2.1       | Apache-2.0                                         | Yes              |
| numpy                     | 2.5.5       | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Yes              |
| olefile                   | 0.47        | BSD-2-Clause                                       | Yes              |
| onnxruntime               | 1.32.0      | MIT                                                | Yes              |
| openpyxl                  | 3.1.5       | MIT                                                | Yes              |
| opentelemetry-api         | 1.45.2      | Apache-2.0                                         | Yes              |
| packaging                 | 26.3        | Apache-2.0 OR BSD-2-Clause                         | Yes              |
| pandas                    | 3.1.1       | BSD-3-Clause                                       | Yes              |
| pdfminer-six              | 20260107    | MIT                                                | Yes              |
| pdfplumber                | 0.11.10     | MIT                                                | Yes              |
| propcache                 | 0.5.4       | Apache-2.0                                         | Yes              |
| protobuf                  | 7.36.2      | BSD-3-Clause                                       | Yes              |
| pycparser                 | 3.12        | BSD-3-Clause                                       | Yes              |
| pydantic-core             | 2.50.4      | MIT                                                | Yes              |
| python-dateutil           | 2.9.0.post0 | Apache-2.0 AND BSD-3-Clause                        | Yes              |
| python-dotenv             | 1.2.4       | BSD-3-Clause                                       | Yes              |
| python-pptx               | 1.0.2       | MIT                                                | Yes              |
| regex                     | 2026.11.15  | Apache-2.0 AND CNRI-Python                         | Yes              |
| requests                  | 2.34.2      | Apache-2.0                                         | Yes              |
| six                       | 1.17.0      | MIT                                                | Yes              |
| soupsieve                 | 4.1.1       | MIT                                                | Yes              |
| starlette                 | 1.10.0      | BSD-3-Clause                                       | Yes              |
| tenacity                  | 9.2.1       | Apache-2.0                                         | Yes              |
| tokie                     | 0.3.0       | MIT OR Apache-2.0                                  | Yes              |
| tqdm                      | 4.70.1      | MPL-2.0 AND MIT                                    | Yes, unmodified  |
| tree-sitter               | 0.26.1      | MIT                                                | Yes              |
| tree-sitter-language-pack | 1.25.2      | MIT                                                | Yes              |
| typing-extensions         | 4.16.0      | PSF-2.0                                            | Yes              |
| typing-inspection         | 0.4.5       | MIT                                                | Yes              |
| tzdata                    | 2026.6      | Apache-2.0                                         | Yes              |
| urllib3                   | 2.8.0       | MIT                                                | Yes              |
| wrapt                     | 2.5.2       | BSD-2-Clause                                       | Yes              |
| xlrd                      | 2.0.2       | BSD-3-Clause                                       | Yes              |
| xlsxwriter                | 3.3.1       | BSD-2-Clause                                       | Yes              |
| yarl                      | 1.25.1      | Apache-2.0                                         | Yes              |

### Notes

- `Commercial`: every license allows commercial use, sale and redistribution of the image. `Yes` means permissive: keep the copyright and license notices. `Yes, unmodified` means weak copyleft: allowed as is; if the component itself is modified, its modified source must be shared under its own license (LGPL-3.0 also requires that users can replace the library, which holds for a pip-installed `pebble`). `Yes, per package` means Debian packages under their own licenses, which ship in the image.
- Apache-2.0 compatibility: no Python package or bundled application is GPL, AGPL or SSPL (strong copyleft or network clauses) or has a non-commercial clause, so none forces a license on Papyrus code or restricts selling the image. The Debian base contains GPL programs (`bash`, `coreutils`, `apt`): Papyrus only runs them as separate programs (mere aggregation), which leaves its license untouched; redistributing them obliges us to offer their source, which Debian publishes for every package version.
- Weak copyleft only, no strong copyleft: `pebble` (LGPL-3.0), LibreOffice, `certifi` and `tqdm` (MPL-2.0) are used unmodified as separate libraries, compatible with distributing Papyrus under Apache-2.0. Modifications to them would have to be released under their own license.
- The published image redistributes LibreOffice, Chromium and Debian packages under their own terms; their license files ship inside the image.
- todo: generate this SBOM in CI (e.g. CycloneDX from `uv.lock` and the image) instead of maintaining it by hand.
- todo: confirm the `fonts-liberation` version in the image (Debian ships 2.x under OFL-1.1, 1.x was GPL-2.0 with font exception).
