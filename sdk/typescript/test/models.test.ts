import assert from 'node:assert/strict'
import { describe, test } from 'node:test'
import { inspect } from 'node:util'
import manifest from '../package.json' with { type: 'json' }
import { APIError, bytes, ConnectionFailed, PapyrusError, type Result, TimedOut, text, VERSION } from '../src/index.ts'
import * as models from '../src/models.ts'

const RESULT: Result = {
  name: 'a.md',
  size: 3,
  mimeType: 'text/markdown',
  extension: 'md',
  label: 'markdown',
  group: 'markup',
  encoding: 'utf-8',
  content: 'hié',
}
const PNG: Result = { ...RESULT, name: 'a.png', mimeType: 'image/png', encoding: 'base64', content: 'iVBORw==' }
const ENVELOPE = { source: { uri: 'file://file' }, options: {}, result: RESULT }

describe('responses', () => {
  test('unknown fields are dropped', () => {
    const parsed = models.markdownResponse.parse({
      ...ENVELOPE,
      options: { quality: 3 },
      source: { uri: 'file://file', sha256: 'abc' },
      links: [],
    })

    assert.deepEqual(parsed, ENVELOPE)
  })

  test('unknown enum values are rejected', () => {
    assert.equal(models.result.safeParse({ ...RESULT, group: 'hologram' }).success, false)
    assert.equal(models.result.safeParse({ ...RESULT, label: 'hologram' }).success, true)
  })

  test('survive JSON and structured clones', () => {
    const parsed = models.renderResponse.parse({ ...ENVELOPE, result: PNG, options: { page: 1, format: 'png' } })

    for (const copy of [JSON.parse(JSON.stringify(parsed)), structuredClone(parsed)]) {
      assert.deepEqual(copy, parsed)
      assert.deepEqual([...bytes(copy.result)], [0x89, 0x50, 0x4e, 0x47])
    }
  })
})

describe('content', () => {
  test('text results', () => {
    assert.equal(text(RESULT), 'hié')
    assert.deepEqual(bytes(RESULT), new TextEncoder().encode('hié'))
  })

  test('binary results have no text', () => {
    assert.deepEqual([...bytes(PNG)], [0x89, 0x50, 0x4e, 0x47])
    assert.throws(() => text(PNG), { name: 'TypeError', message: /a\.png is binary/ })
  })

  test('empty content is content', () => {
    assert.equal(text({ ...RESULT, content: '' }), '')
    assert.equal(bytes({ ...PNG, content: '' }).length, 0)
  })

  test('delivered results have no content', () => {
    const { content: _, encoding: __, ...delivered } = { ...RESULT, uri: 's3://prod/a.md' }

    for (const read of [text, bytes]) {
      assert.throws(() => read(delivered), { name: 'TypeError', message: /s3:\/\/prod\/a\.md/ })
    }
  })
})

describe('errors', () => {
  test('are readable and typed', () => {
    const error = new APIError({
      status: 422,
      code: 'validation_error',
      detail: 'request validation failed',
      errors: [{ loc: ['options', 'page'], msg: 'too small', type: 'greater_than_equal' }],
      requestId: 'req-1',
      retryAfter: 2,
    })

    assert.equal(String(error), 'APIError: 422 validation_error: request validation failed (options.page: too small)')
    assert.equal(new APIError({ status: 413 }).message, '413 error')
    assert.ok(error instanceof PapyrusError && error instanceof Error)
    assert.ok(new TimedOut('x') instanceof ConnectionFailed)
    assert.deepEqual(Object.keys(new TimedOut('x')), [])
    assert.match(inspect(new TimedOut('x')), /^TimedOut: x/)
  })
})

test('the version matches the manifest', () => {
  assert.equal(VERSION, manifest.version)
})
