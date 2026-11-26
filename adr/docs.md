# Documentation

Public documentation of Papyrus on `docs.axelered.com`: who it serves, how it speaks and how it is structured. The
`/document` command reads this file for every docs task; the `axelered-docs` skill defines the bundle contract
(file layout, frontmatter, what Bruno and OpenAPI each own) and wins on layout and ownership.

## Product

- Papyrus turns documents and web pages into LLM-ready Markdown, chunks and images through one HTTP API that runs on
  your own infrastructure.
- Offered as a self-hosted Docker image (`ghcr.io/axelered/papyrus`). The docs only cover self-hosting.
- Out of scope for the docs: the hosted enterprise API (a separate product), pricing, comparisons, sales. The
  landing page links to `https://axelered.com` at most once.

## Readers

| Reader              | Needs                                                          | Served by                       |
|---------------------|----------------------------------------------------------------|---------------------------------|
| AI and agent builder (primary) | clean Markdown and chunks from any file or URL for RAG and LLM context | Introduction, Quickstart, Guides, SDKs |
| Platform engineer (secondary)  | run Papyrus safely in their infrastructure, size it, configure it      | Deploy                           |
| Both                | the request model, formats, errors, endpoint lookup             | Concepts, API reference         |

Buyers are served by the website, not the docs.

## Language

The `/document` defaults, no overrides: US English, second person, present tense, active voice, no hype words, no
emojis. The README keeps its emoji headings; the docs site does not use them.

## Example Values

- URLs: `https://axelered.com`.
- Uploads: bare file names (`sample.pdf`).
- Server: `http://127.0.0.1:8000` with API key `dev`, as started by the quickstart
  (`docker run -p 8000:8000 -e PAPYRUS_API_KEY=dev ghcr.io/axelered/papyrus`).
- Bruno `host` defaults to `http://127.0.0.1:8000` and `token` to `dev`, so the downloaded collection works right
  after the quickstart. Development servers (`docker/dev` on 8001, stage) use gitignored environments
  (`bruno/environments/*.nogit.yml`).
- Bruno responses are captured from a running image and trimmed with `…`; storage examples, which need a connector,
  reuse the sizes of the same document processed locally.

## Sources of Truth

- Contract: `scripts/openapi.py` output, with complete `Field(description=…)` on every wire model.
- Operator settings: `config.example.yml` and the env variables documented in it.
- Endpoint docs and examples: `bruno/`.
- Behavior: the code under `server/` and the operation ADRs in `adr/`.

## Structure

The site renders two tabs per version (see `axelered-docs`).

### Guides (`docs/`)

Flat: every page sits in `docs/`, and the single `docs/meta.json` orders them and groups them with
`---Group---` separators. Every page has a lucide icon name in its `icon` frontmatter.
No "Next steps" section at the end of pages: the site renders previous and next page links.
Titles are short noun phrases, one to three words, because they are also the sidebar labels; the `description`
carries the rest.

| Group        | Reader  | Pages                                                                              |
|--------------|---------|------------------------------------------------------------------------------------|
| (top)        | builder | Introduction (`index`): what Papyrus does, one diagram · Quickstart: Docker run, first Markdown request, first chunked request |
| Markdown     | builder | Markdown extraction · OCR (read modes, backends, page limits) · RAG chunking       |
| Images       | builder | Image rendering · Web screenshots                                                  |
| Utilities    | builder | Inspect · later document tools that are neither Markdown nor images (scan, sign, lock, strip, split, form detection) |
| Concepts     | both    | Request format (`source`, `options`, `result`) · URI formats · Remote storage · File formats · Error codes |
| SDKs         | builder | Python SDK · TypeScript SDK                                                        |
| Deploy       | operator | Docker setup · Configuration · Security (SSRF, sandbox, limits, reverse proxy) · Sizing |

- Guides are grouped by what the reader gets back (Markdown or images); options of an endpoint that need their own workflow, such as OCR and chunking, get their own page in that group.
- Guides explain workflows and link to the API reference; they never list endpoint fields.
- `docs/*.nogit.*` files are local notes and never part of the docs.

### API Reference (generated)

- Built from OpenAPI and `bruno/`; no pages under `docs/api/`.
- Bruno stays flat (no folders) until a new group of endpoints makes the list hard to scan. Order by `info.seq`:
  Markdown, Render, Thumbnail, Screenshot, Inspect, Root, Health, Ready.
- `opencollection.yml` docs are the API overview: the pitch, one paragraph introducing the request model, and
  Authentication. Request format, errors, URIs and formats live in the Concepts guides and are not repeated.
- Endpoint docs are one, at most two, paragraphs of framing: what the endpoint returns and what for, then when to
  pick it over its siblings and the one caveat the reader will hit, in the imperative. No sections, option tables
  or error lists: fields are documented by the schema, requests and responses by the examples, workflows by the
  guides.
- Examples carry a `name` that states the outcome and a one-sentence `description`.

### README

Keeps its pitch, supported formats, API summary, developer and Docker sections, and links to the docs site.
todo: slim the README once the docs site covers its content

## Out of Scope

- Docs for the hosted enterprise API.
- Contributor docs on the site (they stay in the README).
- Bruno folders.
