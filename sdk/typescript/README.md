# Papyrus TypeScript SDK

TypeScript client for [Papyrus](https://docs.axelered.com/papyrus), the self-hosted API that converts documents and web pages into Markdown, chunks and images for LLMs.

```shell
npm install @axelered/papyrus
```

Node 22.12 or later, or Bun. It uses web standard APIs only, so Deno, edge runtimes and browsers work too but are not tested. SDK `X.Y` is made for Papyrus server `X.Y`.

## Usage

```ts
import { openAsBlob } from 'node:fs'
import { writeFile } from 'node:fs/promises'
import { bytes, Papyrus, text } from '@axelered/papyrus'

const client = new Papyrus({ baseUrl: 'http://127.0.0.1:8000', apiKey: 'dev' })
const file = await openAsBlob('sample.pdf')

const doc = await client.markdown(file, { filename: 'sample.pdf', chunking: { maxTokens: 512 } })
console.log(text(doc.result))
for (const chunk of doc.chunks ?? []) console.log(chunk.headings, chunk.tokens)

const page = await client.render(file, { page: 1, format: 'png' })
await writeFile('page.png', bytes(page.result))
```

| Method | Returns |
|---|---|
| `markdown` | Markdown of a document or web page, from its text or page images per `readMode` (`auto` by default), chunked with `chunking` |
| `render` | One page or image frame at a readable size |
| `thumbnail` | One page, image or web page fitted into a box |
| `screenshot` | A web page captured in headless Chromium |
| `inspect` | Detected format and checks of a document |
| `health`, `ready` | Server probes; they do not verify the API key |

Responses are plain, typed objects with `source`, `options` and, except for `inspect`, `result`. Read the artifact with `text(result)` (Markdown) or `bytes(result)` (`Uint8Array`). With `destination` the server writes it to storage and only `result.uri` is set.

## Input

| Value | Meaning |
|---|---|
| `string`, `URL` | Always a URI, sent as is; a local path is a `TypeError` |
| `Blob`, `File` | An upload: `openAsBlob(path)` from `node:fs`, `Bun.file(path)`, a file input |
| `ArrayBuffer`, `Uint8Array`, `Buffer` | An upload |

`filename` names an upload that has no name of its own; its extension refines format detection.

## Configuration

| Option | Default |
|---|---|
| `baseUrl` | `PAPYRUS_BASE_URL`, required |
| `apiKey` | `PAPYRUS_API_KEY`, no authorization when unset |
| `timeout` | 310000 milliseconds for one attempt, also per call. Keep it above the server's `limits.deadline` |
| `maxRetries` | 2, only for requests the server did not process |
| `fetch` | The global one; replace it for proxies, private CAs and longer timeouts |

Every operation also takes a `signal`; an abort rejects with its reason.

**Node's `fetch` gives up after 300 seconds whatever `timeout` is.** If your server has a longer `limits.deadline`, pass a `fetch` with an `undici` agent. Use `undici@7`, which works with Node 22 and later:

```ts
import { Agent } from 'undici'

const extra: object = { dispatcher: new Agent({ headersTimeout: 0, bodyTimeout: 0 }) }
const client = new Papyrus({
  timeout: 900_000,
  fetch: (url, init) => fetch(url, { ...init, ...extra }),
})
```

Do not pass the `fetch` exported by `undici`: it sends uploads as the text `[object FormData]`.

**In a browser the API key is visible to whoever opens the page.** Browsers also need the page's origin in the server's `cors.allow_origins`.

## Errors

```ts
import { APIError, ConnectionFailed } from '@axelered/papyrus'

try {
  await client.markdown('s3://prod/docs/report.pdf')
} catch (error) {
  if (error instanceof APIError) console.log(error.status, error.code, error.detail, error.requestId)
  else if (error instanceof ConnectionFailed) console.log('server unreachable or timed out')
  else throw error
}
```

Branch on `code`, listed in [error codes](https://docs.axelered.com/papyrus/errors). Every failed request rejects with a `PapyrusError`, except aborts and files that cannot be read.

## License

Apache License 2.0. Copyright Axelered D.o.o. and Axelered LLC-Fz.
