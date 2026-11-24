# TypeScript SDK

Official TypeScript client of the Papyrus API, published on npm as `@axelered/papyrus`. It follows `adr/sdk-python.md`: what is not stated here (options, `output=json`, errors, retries, probes, versions) is the same.

## Decisions

| Concern | Choice |
|---|---|
| Implementation | Handwritten, as the Python SDK. Not generated: `openapi-typescript` gives types and validates nothing |
| Runtimes | Web standard APIs only (`fetch`, `FormData`, `Blob`), never the disk. Node >= 22.12 and Bun are tested; Deno, edge runtimes and browsers are supported, not tested |
| Client | One `Papyrus` class, everything is asynchronous |
| HTTP | Global `fetch`, replaceable. `undici` rejected: Node only. `ky` and `ofetch` rejected: same `fetch`, same limits, and retries to rewrite anyway |
| Models | `zod/mini` schemas, types derived with `z.infer` |
| Location | `sdk/typescript/`, standalone project, tests inside |
| Package | ESM only: `require()` of ESM works from Node 22.12, so no second build |
| Build | `tsc`. Vite rejected: it does not emit declarations, so `tsc` is needed anyway, and `vite-plugin-dts` fails on TypeScript 7. `tsdown` if several entry points or a CDN build are needed |
| Tools | `bun` (packages), `node:test` and `node:assert` (they run on Node and Bun), Biome (format and lint) |
| Dependencies | `zod` only; `typescript`, `@types/node` and `@biomejs/biome` in development |

## Layout

```
sdk/typescript/
  package.json, bun.lock, tsconfig.json, biome.json
  README.md          npm page
  LICENSE, NOTICE    as sdk/python
  src/
    index.ts         public names
    client.ts        Papyrus: I/O and retry loop only
    wire.ts          request building, response parsing, retry decision
    models.ts        schemas and types, text(), bytes()
    errors.ts
    version.ts
  test/
```

- Sources run unbuilt on Node and Bun: relative imports end in `.ts`, `erasableSyntaxOnly`, and `rewriteRelativeImportExtensions` for the build.
- zod is imported as `import * as z from 'zod/mini'`: about 5 KB gzipped, `import { z }` is not tree-shaken and takes about 90 KB.
- `version.ts` and `package.json` keep `0.0.0` in git; a test checks that they match.
- `node_modules/` is added to `.gitignore`. `dist/` and `openapi.json` are already ignored.

## Client

```ts
import { openAsBlob } from 'node:fs'
import { bytes, Papyrus, text } from '@axelered/papyrus'

const client = new Papyrus({ baseUrl: 'http://127.0.0.1:8000', apiKey: 'dev' })

const doc = await client.markdown(await openAsBlob('sample.pdf'), {
  filename: 'sample.pdf',
  chunking: { maxTokens: 512 },
})
text(doc.result)
doc.chunks

const page = await client.render('https://axelered.com', { page: 1, format: 'png' })
bytes(page.result)
```

| Field | Default | Notes |
|---|---|---|
| `baseUrl` | `PAPYRUS_BASE_URL`, else `TypeError` | `http(s)://` |
| `apiKey` | `PAPYRUS_API_KEY`, else no header | Same checks as Python, the key is never echoed |
| `timeout` | `310_000` ms | Total for one attempt: connection, upload, wait and read. Also per call |
| `maxRetries` | 2 | Negative is a `RangeError` |
| `fetch` | global | For proxies, private CAs, longer timeouts and tests |

- The environment is read through `globalThis.process?.env`, absent in browsers.
- The key is in a `#private` field: not in `console.log`, `util.inspect` or `JSON.stringify`.
- `redirect: 'manual'`. No `close()`, the connection pool belongs to the runtime.
- `User-Agent: papyrus-typescript/<version>`, not sent by browsers: it is not among the server's CORS headers and would fail the preflight.
- The constructor throws, methods reject.

### Timeouts

`fetch` has no separate connect and read timeouts, so `timeout` is total. The server's deadline covers the upload too, so the two values stay comparable.

- **Node stops at about 301 s** whatever `timeout` is (`UND_ERR_HEADERS_TIMEOUT`, measured on Node 26: answers at 300.5 s arrive, at 301.5 s do not). With the default `limits.deadline` of 300 s `deadline_exceeded` still comes first, by less than a second. It becomes `TimedOut`, with a message pointing to the recipe below.
- Raising `limits.deadline` on Node needs a custom `fetch`:

```ts
import { Agent } from 'undici'

const dispatcher = new Agent({ headersTimeout: 0, bodyTimeout: 0 })
const client = new Papyrus({
  timeout: 900_000,
  fetch: (url, init) => fetch(url, { ...init, dispatcher }),
})
```

- The `fetch` exported by `undici` must not be passed: it does not recognize the global `FormData` and silently sends `[object FormData]` as text. README and guide say so.
- `signal` per call, combined with the timeout by `AbortSignal.any`, also interrupts the wait between retries. An abort rejects with `signal.reason` unchanged, not with a `PapyrusError`.

## Operations

`client.<operation>(input, { ...options, destination, filename, timeout, signal })`, one flat object. `screenshot(url, …)` takes a `string` or `URL` only, `inspect` has no `destination`, `health` and `ready` accept `signal`.

- Names are camelCase as on the wire, no conversion.
- The client takes its own four fields and sends all the others: a typo in plain JavaScript is the server's `422`, never ignored. `undefined` is not sent.
- Input option types are `Partial` of the echoed options (`Partial<RenderOptions>`, `chunking` included), so every option is written once.

### Input

| Type | Meaning |
|---|---|
| `string`, `URL` | Always a URI, sent as is. Without `://` a `TypeError` explains how to upload a file |
| `Blob`, `File`, `Bun.file()` | Upload |
| `ArrayBuffer`, `Uint8Array` (`Buffer` included) | Upload |
| Anything else, `ReadableStream` included | `TypeError`: a stream has no known length and a retry could not read it again |

- A string is never a local path: the untrusted input risk of the Python SDK does not exist here, and `source=` and `file=` are not needed.
- Types are checked at runtime too.
- `filename` defaults to the last segment of `File.name` (`Bun.file` has a whole path there), else `file`. `TypeError` with a URI. It matters: the server ignores the part's `Content-Type` and refines detection by extension.

## Models

- Responses are validated. Unknown fields are ignored, unknown enum values are `InvalidResponse`, `label` is a `string`, bounds are not copied.
- Absent facts are `undefined`, never `null`: the server omits them (`exclude_none`). Echoed options that always have a value are required.
- Enums are literal unions. Types are exported, schemas are not. The declarations do import zod's types: avoiding it would mean writing every type twice.
- `requestId` is a plain field of the response.

### Content

Responses are plain objects, unlike Python: class instances lose their methods in `structuredClone`, `postMessage` and caches, and React refuses them from Server to Client Components.

- `text(result)` is a `string`, `TypeError` when binary. `bytes(result)` is a `Uint8Array`, decoded per `encoding`.
- Both throw `TypeError` when the result was written to a `destination`, pointing to `result.uri`.
- No `save()`, the SDK does not touch the disk.
- base64 is decoded with `atob` and a loop (22 MB in 34 ms); `Uint8Array.fromBase64` is missing on Node 22.

## Errors

Same tree and meaning as Python: `PapyrusError`, `ConnectionFailed`, `TimedOut`, `InvalidResponse`, `APIError(status, code, detail, errors, requestId, retryAfter)`. The original error is `cause`.

- `code` is `ProblemCode | (string & {}) | undefined`: known codes are suggested, new ones are not rejected.
- `retryAfter` is in seconds, as the header.
- A redirect is an `APIError` with its status; browsers hide it, the status is `0`.

## Retries

Same table as Python. `fetch` reports every connection failure as a `TypeError`, so a failure before the connection is recognized by a closed list of codes: `ECONNREFUSED`, `ENOTFOUND`, `EAI_AGAIN`, `UND_ERR_CONNECT_TIMEOUT` (`cause.code` on Node) and their Bun equivalents. Any other code, and every failure in browsers, is `ConnectionFailed` and is not retried.

- No connect timeout of its own: `undici` has 10 s on Node, elsewhere an unreachable host can take the whole `timeout`.
- `undici` drops idle connections at 4 s, below uvicorn's 5 s.
- The same `FormData` can be sent again, file-backed blobs included (checked on Node and Bun).
- In browsers `Retry-After` is not readable (CORS), only the backoff applies.
- todo: during implementation, verify connection error codes and idle connection reuse on Bun against a real uvicorn, and reconcile this section.

## Browsers

Allowed where the server sets `cors.allow_origins`, with no opt-in flag. README and guide warn that the API key is visible to whoever opens the page.

## Tests

`sdk/typescript/test/`, on Node 22 and Bun.

1. `schema.test.ts`: `z.toJSONSchema` against the app's OpenAPI. Components, fields, enums, problem codes, `destination` and one method per `/v1/*` path. A field added only to the server fails, naming where to add it; so does a server option named as a client field (`filename`, `destination`, `timeout`, `signal`).
2. `client.test.ts`: fake `fetch` and a local `node:http` server. Retries, timeouts, `signal`, connection codes, redirects, non `problem+json` errors, inputs, configuration, key secrecy.
3. `models.test.ts`: `text`, `bytes`, unknown fields and enum values, responses through `structuredClone` and JSON.
4. `integration.test.ts`: every operation against a `papyrus:dev` container, with `Blob`, `Uint8Array` and `File`, a non-ASCII file name, real errors, `health` and `ready`. The SSRF guard refuses local pages, so for `screenshot` it checks the refusal; a successful capture is covered by the fake `fetch`.

- Script `test` runs 1 to 3, `test:integration` runs 4. A missing OpenAPI document or server fails, it does not skip: a silent skip would hide drift.
- Script `test:package`: `npm pack`, install in a clean folder, `import` and `require` on Node 22, typecheck of a consumer.
- dekit task `sdk/typescript`: exports OpenAPI with `scripts/openapi.py`, starts the container, runs typecheck, Biome and the tests on Node and Bun.

## Documentation

- `sdk/typescript/README.md`: install, example, input per runtime, errors, the `Agent` recipe, the `undici` `fetch` and browser key warnings.
- `docs/typescript.mdx` in the Guides group after `python`, with `docs/meta.json` and the structure table of `adr/docs.md`. Docs go out with every tag, so the package must be on npm before the next one: until then `npm install @axelered/papyrus` fails. It needs the `axelered` organization on npm.
- Bruno is unaffected, the API does not change.

## Out of Scope

- todo: publishing to npm. Stamp `version.ts` and `package.json` in the semver step, run `test:package`, Trusted Publishing.
- todo: `check.yml` does not run the TypeScript tests, so drift is caught only locally. Owned by the user.
- todo: `Retry-After` in `EXPOSE_HEADERS` of `server/utils/cors.py`.
- todo: add `zod` and the development tools to `adr/license.md` once `feat/license` is merged.
- todo: local paths (a `/node` entry point), streaming input and output, CDN build.
