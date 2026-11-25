import assert from 'node:assert/strict'
import { openAsBlob } from 'node:fs'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, test } from 'node:test'
import { inspect } from 'node:util'
import {
  APIError,
  ConnectionFailed,
  type Input,
  InvalidResponse,
  Papyrus,
  PapyrusError,
  type PapyrusOptions,
  TimedOut,
  VERSION,
} from '../src/index.ts'
import {
  closedUrl,
  FakeServer,
  failure,
  INSPECTED,
  listen,
  listenTcp,
  ok,
  problem,
  recordSleeps,
  URI,
} from './server.ts'

const ENV = ['PAPYRUS_BASE_URL', 'PAPYRUS_API_KEY']
let saved: (string | undefined)[]

beforeEach(() => {
  saved = ENV.map((name) => process.env[name])
  for (const name of ENV) delete process.env[name]
})

afterEach(() => {
  ENV.forEach((name, i) => {
    const value = saved[i]
    if (value === undefined) delete process.env[name]
    else process.env[name] = value
  })
})

async function caught(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(
    () => assert.fail('did not reject'),
    (error) => error,
  )
}

async function apiError(promise: Promise<unknown>): Promise<APIError> {
  const error = await caught(promise)
  assert.ok(error instanceof APIError, String(error))
  return error
}

describe('requests', () => {
  test('headers and url', async () => {
    const server = new FakeServer(ok())

    await server.client({ baseUrl: 'http://papyrus/prefix/' }).inspect(URI)

    const [sent] = server.sent
    assert.equal(sent?.url, 'http://papyrus/prefix/v1/inspect')
    assert.equal(sent?.headers.get('authorization'), 'Bearer dev')
    assert.equal(sent?.headers.get('user-agent'), `papyrus-typescript/${VERSION}`)
    assert.equal(sent?.headers.get('content-type'), 'application/json')
    assert.equal(sent?.init.redirect, 'manual')
  })

  test('only set options are sent', async () => {
    const server = new FakeServer(problem(500, 'internal_error'))
    const client = server.client()

    await apiError(client.inspect(URI))
    await apiError(client.markdown(URI, { chunking: { maxTokens: 99 }, timeout: 1000 }))
    await apiError(client.markdown(URI, { readMode: 'ocr' }))
    await apiError(client.screenshot(URI, { fullPage: false, width: undefined, destination: 's3://prod/a.png' }))

    assert.deepEqual(
      server.sent.map(({ text }) => JSON.parse(text)),
      [
        { source: URI, options: {} },
        { source: URI, options: { chunking: { maxTokens: 99 } }, output: 'json' },
        { source: URI, options: { readMode: 'ocr' }, output: 'json' },
        { source: URI, options: { fullPage: false }, output: 'json', destination: 's3://prod/a.png' },
      ],
    )
  })

  test('unknown options reach the server', async () => {
    const server = new FakeServer(problem(422, 'validation_error'))
    const typo = { pgae: 2 }

    await apiError(server.client().render(URI, typo as object))

    assert.deepEqual(JSON.parse(server.sent[0]?.text ?? '').options, typo)
  })

  test('the request id of the response is kept', async () => {
    const traced = await new FakeServer(ok(INSPECTED, { 'x-request-id': 'req-1' })).client().inspect(URI)
    const untraced = await new FakeServer(ok()).client().inspect(URI)

    assert.deepEqual(traced, { ...INSPECTED, requestId: 'req-1' })
    assert.deepEqual(untraced, INSPECTED)
  })
})

describe('input', () => {
  test('urls are sent as URIs', async () => {
    const server = new FakeServer(ok())

    await server.client().inspect(new URL('https://axelered.com/a?b=c'))
    await server.client().inspect('s3://prod/à b.pdf')

    assert.deepEqual(
      server.sent.map(({ text }) => JSON.parse(text).source),
      ['https://axelered.com/a?b=c', 's3://prod/à b.pdf'],
    )
  })

  const bytes = new TextEncoder().encode('content')
  const uploads: [string, Input, string | undefined, string][] = [
    ['Uint8Array', bytes, undefined, 'file'],
    ['Uint8Array with a name', bytes, 'a.csv', 'a.csv'],
    ['a view on part of a buffer', new TextEncoder().encode('--content--').subarray(2, 9), undefined, 'file'],
    ['Buffer', Buffer.from('content'), undefined, 'file'],
    ['ArrayBuffer', bytes.buffer, undefined, 'file'],
    ['Blob', new Blob(['content']), undefined, 'file'],
    ['File', new File(['content'], 'a.csv'), undefined, 'a.csv'],
    ['File with another name', new File(['content'], 'a.csv'), 'b.csv', 'b.csv'],
    ['File named by a path', new File(['content'], 'dir\\sub/a.csv'), undefined, 'a.csv'],
  ]
  for (const [name, content, filename, expected] of uploads) {
    test(`uploads ${name}`, async () => {
      const server = new FakeServer(ok())

      await server.client().inspect(content, { filename })

      const [sent] = server.sent
      const form = await new Response(sent?.text, { headers: sent?.headers ?? {} }).formData()
      const file = form.get('file')
      assert.deepEqual(JSON.parse(String(form.get('metadata'))), { options: {} })
      assert.ok(file instanceof Blob)
      assert.equal(await file.text(), 'content')
      assert.equal('name' in file && file.name, expected)
    })
  }

  const invalid: [unknown, object, RegExp][] = [
    ['sample.pdf', {}, /always a URI.*openAsBlob/],
    ['', {}, /always a URI/],
    [URI, { filename: 'a.pdf' }, /uploads only/],
    [42, {}, /cannot send number/],
    [null, {}, /cannot send null/],
    [undefined, {}, /cannot send undefined/],
    [{}, {}, /cannot send Object/],
    [new ReadableStream(), {}, /cannot send ReadableStream/],
  ]
  for (const [input, params, message] of invalid) {
    test(`refuses ${inspect(input)} ${inspect(params)}`, async () => {
      const server = new FakeServer(ok())

      await assert.rejects(server.client().markdown(input as string, params), { name: 'TypeError', message })
      assert.equal(server.sent.length, 0)
    })
  }

  test('screenshot takes URLs only', async () => {
    const client = new FakeServer(ok()).client()

    await assert.rejects(client.screenshot(new Blob(['x']) as never), { name: 'TypeError', message: /Blob/ })
    await assert.rejects(client.screenshot('axelered.com'), { name: 'TypeError', message: /no scheme/ })
  })
})

describe('configuration', () => {
  test('comes from the environment', async () => {
    process.env.PAPYRUS_BASE_URL = 'http://from-env'
    process.env.PAPYRUS_API_KEY = 'env-key'
    const server = new FakeServer(ok())

    await new Papyrus({ fetch: server.fetch }).inspect(URI)

    assert.equal(server.sent[0]?.url, 'http://from-env/v1/inspect')
    assert.equal(server.sent[0]?.headers.get('authorization'), 'Bearer env-key')
  })

  test('no key sends no authorization', async () => {
    const server = new FakeServer(ok())

    await server.client({ apiKey: undefined }).inspect(URI)

    assert.equal(server.sent[0]?.headers.has('authorization'), false)
  })

  const invalid: [PapyrusOptions, string, RegExp][] = [
    [{ baseUrl: undefined }, 'TypeError', /PAPYRUS_BASE_URL/],
    [{ baseUrl: 'papyrus:8000' }, 'TypeError', /http:\/\//],
    [{ baseUrl: 'http://' }, 'TypeError', /http:\/\//],
    [{ baseUrl: 'http://user:secret@papyrus' }, 'TypeError', /credentials/],
    [{ maxRetries: -1 }, 'RangeError', /maxRetries/],
    [{ maxRetries: Number.NaN }, 'RangeError', /maxRetries/],
    [{ timeout: 0 }, 'RangeError', /timeout/],
    [{ timeout: Number.POSITIVE_INFINITY }, 'RangeError', /timeout/],
    [{ apiKey: 'secret-a,secret-b' }, 'TypeError', /comma/],
    [{ apiKey: 'secret\n' }, 'TypeError', /whitespace/],
    [{ apiKey: 'secrèt' }, 'TypeError', /characters/],
  ]
  for (const [options, name, message] of invalid) {
    test(`refuses ${inspect(options)}`, () => {
      assert.throws(
        () => new FakeServer(ok()).client(options),
        (error) => {
          assert.ok(error instanceof Error)
          assert.equal(error.name, name)
          assert.match(error.message, message)
          assert.doesNotMatch(error.message, /secr/)
          return true
        },
      )
    })
  }

  test('a call timeout is checked too', async () => {
    await assert.rejects(new FakeServer(ok()).client().inspect(URI, { timeout: -1 }), { name: 'RangeError' })
  })

  test('the key is never shown', async () => {
    const client = new FakeServer(problem(401, 'unauthorized')).client({ apiKey: 'hidden' })

    const error = await apiError(client.inspect(URI))

    const shown = [client, error].map((value) => inspect(value, { showHidden: true, depth: 9 }) + JSON.stringify(value))
    assert.doesNotMatch(shown.join(), /hidden/)
  })
})

describe('errors', () => {
  test('carry the problem', async () => {
    const response = problem(503, 'not_ready', { 'x-request-id': 'req-1', 'retry-after': '2' })

    const error = await apiError(new FakeServer(response).client().inspect(URI))

    assert.deepEqual(
      [error.status, error.code, error.detail, error.requestId, error.retryAfter, error.errors],
      [503, 'not_ready', 'why', 'req-1', 2, []],
    )
  })

  const foreign = [
    new Response('<html>nginx</html>', { status: 413 }),
    new Response('{', { status: 413, headers: { 'content-type': 'application/problem+json' } }),
    new Response('{"title": "x"}', { status: 413, headers: { 'content-type': 'application/problem+json' } }),
  ]
  for (const [i, response] of foreign.entries()) {
    test(`that are not Papyrus problems have no code (${i})`, async () => {
      const error = await apiError(new FakeServer(response).client().inspect(URI))

      assert.deepEqual([error.status, error.code, error.errors], [413, undefined, []])
    })
  }

  const unexpected = [
    new Response('<html>login</html>'),
    ok({ ...INSPECTED, source: { uri: 'x', group: 'hologram' } }),
    ok(null),
    new Response(null, { status: 204 }),
  ]
  for (const [i, response] of unexpected.entries()) {
    test(`unexpected success is invalid (${i})`, async () => {
      const error = await caught(new FakeServer(response).client().inspect(URI))

      assert.ok(error instanceof InvalidResponse)
      assert.match(error.message, /\/v1\/inspect/)
      assert.ok(error.cause instanceof Error)
    })
  }

  for (const value of ['Wed, 21 Oct 2026 07:28:00 GMT', '-1', '']) {
    test(`retry-after "${value}" is ignored`, async () => {
      const error = await apiError(
        new FakeServer(problem(503, 'not_ready', { 'retry-after': value })).client().inspect(URI),
      )

      assert.equal(error.retryAfter, undefined)
    })
  }
})

describe('retries', () => {
  const untouched: [string, Response | Error][] = [
    ['queue_full', problem(503, 'queue_full')],
    ['a proxy 502', new Response('bad gateway', { status: 502 })],
    ['a proxy 503', new Response('unavailable', { status: 503 })],
    ['ECONNREFUSED', failure('ECONNREFUSED')],
    ['ECONNREFUSED on Bun', failure('ECONNREFUSED', 'bun')],
    ['ENOTFOUND', failure('ENOTFOUND')],
    ['EAI_AGAIN', failure('EAI_AGAIN')],
    ['a connect timeout', failure('UND_ERR_CONNECT_TIMEOUT')],
  ]
  for (const [name, outcome] of untouched) {
    test(`after ${name}`, async (t) => {
      const sleeps = recordSleeps(t)
      const server = new FakeServer(outcome, outcome, ok())

      await server.client().inspect(new Blob(['content']))

      assert.equal(server.sent.length, 3)
      assert.ok(server.sent.every(({ text }) => text.includes('content')))
      const [first = 0, second = 0] = sleeps
      assert.ok(first >= 500 && first <= 750 && second >= 1000 && second <= 1500, String(sleeps))
    })
  }

  const worked: [string, Response | Error, abstract new (...args: never) => PapyrusError][] = [
    ['processing_failed', problem(500, 'processing_failed'), APIError],
    ['source_fetch_failed', problem(502, 'source_fetch_failed'), APIError],
    ['destination_failed', problem(502, 'destination_failed'), APIError],
    ['not_ready', problem(503, 'not_ready'), APIError],
    ['ocr_failed', problem(502, 'ocr_failed'), APIError],
    ['ocr_rate_limited', problem(503, 'ocr_rate_limited', { 'retry-after': '30' }), APIError],
    ['deadline_exceeded', problem(504, 'deadline_exceeded'), APIError],
    ['a proxy 504', new Response('gateway timeout', { status: 504 }), APIError],
    ['a proxy 500', new Response('oops', { status: 500 }), APIError],
    ['ECONNRESET', failure('ECONNRESET'), ConnectionFailed],
    ['ECONNRESET on Bun', failure('ECONNRESET', 'bun'), ConnectionFailed],
    ['a lost socket', failure('UND_ERR_SOCKET'), ConnectionFailed],
    ['the Node header timeout', failure('UND_ERR_HEADERS_TIMEOUT'), TimedOut],
    ['the Node body timeout', failure('UND_ERR_BODY_TIMEOUT'), TimedOut],
    ['a failure without a code', new TypeError('Failed to fetch'), ConnectionFailed],
  ]
  for (const [name, outcome, kind] of worked) {
    test(`never after ${name}`, async (t) => {
      const sleeps = recordSleeps(t)
      const server = new FakeServer(outcome, ok())

      const error = await caught(server.client().inspect(URI))

      assert.ok(error instanceof kind, String(error))
      assert.equal(server.sent.length, 1)
      assert.deepEqual(sleeps, [])
      if (outcome instanceof Error) assert.equal(error.cause, outcome)
    })
  }

  test('the Node limit is explained', async () => {
    const error = await caught(new FakeServer(failure('UND_ERR_HEADERS_TIMEOUT')).client().inspect(URI))

    assert.match(String(error), /pass a fetch with longer timeouts/)
  })

  test('give up', async (t) => {
    const sleeps = recordSleeps(t)
    const server = new FakeServer(problem(503, 'queue_full'))

    const error = await apiError(server.client({ maxRetries: 1 }).inspect(URI))

    assert.equal(error.code, 'queue_full')
    assert.deepEqual([server.sent.length, sleeps.length], [2, 1])
  })

  test('can be disabled', async () => {
    const server = new FakeServer(failure('ECONNREFUSED'))

    await assert.rejects(server.client({ maxRetries: 0 }).inspect(URI), ConnectionFailed)

    assert.equal(server.sent.length, 1)
  })

  for (const [asked, low, high] of [
    ['4', 4000, 6000],
    ['3600', 30_000, 30_000],
  ] as const) {
    test(`honor retry-after ${asked} up to a cap`, async (t) => {
      const sleeps = recordSleeps(t)

      await new FakeServer(problem(503, 'queue_full', { 'retry-after': asked }), ok()).client().inspect(URI)

      const [wait = 0] = sleeps
      assert.ok(wait >= low && wait <= high, String(wait))
    })
  }
})

describe('probes', () => {
  test('health and ready', async () => {
    const up = new FakeServer(ok({ status: 'ok' }, { 'x-request-id': 'req-1' })).client()
    const busy = new FakeServer(problem(503, 'not_ready')).client()

    assert.deepEqual(await up.health(), { status: 'ok' })
    assert.equal(await up.ready(), true)
    assert.equal(await busy.ready(), false)
    await assert.rejects(busy.health(), { code: 'not_ready' })
  })

  for (const outcome of [failure('ECONNREFUSED'), new Response('bad gateway', { status: 502 })]) {
    test(`reject without retrying after ${outcome instanceof Error ? 'a failure' : 'a 502'}`, async () => {
      const server = new FakeServer(outcome)

      await assert.rejects(server.client().health(), PapyrusError)
      await assert.rejects(server.client().ready(), PapyrusError)

      assert.deepEqual(
        server.sent.map(({ url }) => url),
        ['http://papyrus/health', 'http://papyrus/ready'],
      )
    })
  }
})

// - - - over real sockets, where the runtimes differ

describe('network', () => {
  test('redirects are not followed', async (t) => {
    let hits = 0
    const baseUrl = await listen(t, (_request, response) => {
      hits++
      response.writeHead(302, { location: '/elsewhere' }).end()
    })

    const error = await apiError(new Papyrus({ baseUrl }).inspect(URI))

    assert.deepEqual([error.status, hits], [302, 1])
  })

  for (const [name, url, code] of [
    ['a refused connection', closedUrl, /ECONNREFUSED/],
    ['an unknown host', async () => 'http://papyrus.invalid', /ENOTFOUND|EAI_AGAIN/],
  ] as const) {
    test(`${name} is retried and fails`, async () => {
      const client = new Papyrus({ baseUrl: await url(), maxRetries: 1 })
      const started = performance.now()

      const error = await caught(client.inspect(URI))

      assert.ok(error instanceof ConnectionFailed && !(error instanceof TimedOut), String(error))
      assert.match(error.message, code)
      assert.ok(performance.now() - started >= 490, 'did not wait for a second attempt')
    })
  }

  test('a lost connection is not retried', async (t) => {
    let hits = 0
    const baseUrl = await listenTcp(t, (socket) => {
      hits++
      socket.resetAndDestroy()
    })

    await assert.rejects(new Papyrus({ baseUrl }).inspect(URI), ConnectionFailed)

    assert.equal(hits, 1)
  })

  test('a body cut short is a lost connection', async (t) => {
    const baseUrl = await listenTcp(t, (socket) => {
      socket.write('HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{"')
      setTimeout(() => socket.destroy(), 20)
    })

    await assert.rejects(new Papyrus({ baseUrl }).inspect(URI), ConnectionFailed)
  })

  test('the timeout covers the wait and the body', async (t) => {
    const silent = await listen(t, () => {})
    const stalled = await listen(t, (_request, response) => {
      response.writeHead(200, { 'content-type': 'application/json' }).write('{')
    })

    for (const baseUrl of [silent, stalled]) {
      const error = await caught(new Papyrus({ baseUrl, timeout: 50 }).inspect(URI))
      assert.ok(error instanceof TimedOut, String(error))
      assert.match(error.message, /50 ms/)
      await assert.rejects(new Papyrus({ baseUrl }).inspect(URI, { timeout: 50 }), TimedOut)
    }
  })

  test('an abort rejects with its reason', async (t) => {
    let hits = 0
    const baseUrl = await listen(t, () => {
      hits++
    })
    const reason = new Error('mine')
    const client = new Papyrus({ baseUrl })

    const controller = new AbortController()
    const running = caught(client.inspect(URI, { signal: controller.signal }))
    setTimeout(() => controller.abort(reason), 50)

    assert.equal(await running, reason)
    assert.equal(await caught(client.inspect(URI, { signal: AbortSignal.abort(reason) })), reason)
    assert.equal(await caught(client.ready({ signal: AbortSignal.abort(reason) })), reason)
    assert.equal(hits, 1)
  })

  test('an abort interrupts the wait between attempts', async () => {
    const reason = new Error('mine')
    const server = new FakeServer(problem(503, 'queue_full', { 'retry-after': '20' }), ok())
    const controller = new AbortController()

    const running = caught(server.client().inspect(URI, { signal: controller.signal }))
    setTimeout(() => controller.abort(reason), 20)

    assert.equal(await running, reason)
    assert.equal(server.sent.length, 1)
  })

  test('a deleted file rejects before anything is sent', async (t) => {
    const dir = await mkdtemp(join(tmpdir(), 'papyrus-'))
    t.after(() => rm(dir, { recursive: true }))
    await writeFile(join(dir, 'a.csv'), 'content')
    const blob = await openAsBlob(join(dir, 'a.csv'))
    await rm(join(dir, 'a.csv'))
    let hits = 0
    const baseUrl = await listen(t, () => {
      hits++
    })

    const error = await caught(new Papyrus({ baseUrl }).inspect(blob))

    assert.ok(error instanceof Error && !(error instanceof PapyrusError), String(error))
    assert.equal(hits, 0)
  })

  test('files are sent whole on every attempt', async (t) => {
    const dir = await mkdtemp(join(tmpdir(), 'papyrus-'))
    t.after(() => rm(dir, { recursive: true }))
    await writeFile(join(dir, 'a.csv'), 'x'.repeat(300_000))
    const lengths: number[] = []
    const baseUrl = await listen(t, async (request, response) => {
      let length = 0
      for await (const part of request) length += part.length
      lengths.push(length)
      response.writeHead(lengths.length < 2 ? 503 : 200, { 'content-type': 'application/json' })
      response.end(JSON.stringify(INSPECTED))
    })

    await new Papyrus({ baseUrl }).inspect(await openAsBlob(join(dir, 'a.csv')), { filename: 'a.csv' })

    assert.equal(lengths.length, 2)
    assert.ok(lengths.every((length) => length > 300_000))
  })
})
