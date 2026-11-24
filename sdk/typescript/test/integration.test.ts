import assert from 'node:assert/strict'
import { openAsBlob } from 'node:fs'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, test } from 'node:test'
import { APIError, bytes, Papyrus, text } from '../src/index.ts'

// a real server, from PAPYRUS_BASE_URL and PAPYRUS_API_KEY; unset fails, a skip would hide drift
const client = new Papyrus()

const CSV = new Blob(['name,price\npen,2\n'])
const HTML = new Blob(['<!doctype html><html><head><title>Hi</title></head><body><h1>Hello</h1></body></html>'])
const RTF = new Blob(['{\\rtf1\\ansi Hello from rtf}'])
const PDF = pdf(2)

/** Blank pages of 200x100 points */
function pdf(pages: number): Uint8Array {
  const kids = Array.from({ length: pages }, (_, i) => `${i + 3} 0 R`).join(' ')
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    `<< /Type /Pages /Kids [${kids}] /Count ${pages} >>`,
    ...Array.from({ length: pages }, () => '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] >>'),
  ]
  let body = '%PDF-1.4\n'
  const offsets = objects.map((object, i) => {
    const offset = body.length
    body += `${i + 1} 0 obj\n${object}\nendobj\n`
    return `${String(offset).padStart(10, '0')} 00000 n \n`
  })
  const xref = `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.join('')}`
  const trailer = `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${body.length}\n%%EOF\n`
  return new TextEncoder().encode(body + xref + trailer)
}

function magic(content: Uint8Array): string {
  return String.fromCharCode(...content.subarray(0, 4))
}

describe('operations', () => {
  test('markdown of a Blob, named by the caller', async () => {
    const doc = await client.markdown(CSV, { filename: 'prezzi è.csv' })

    assert.match(text(doc.result), /\| pen \| 2 \|/)
    assert.deepEqual([doc.source.name, doc.source.label], ['prezzi è.csv', 'csv'])
    assert.deepEqual(doc.options, {})
    assert.equal(doc.chunks, undefined)
    assert.ok(doc.requestId)
  })

  test('chunks of a File', async () => {
    const file = new File(['# One\n\nfirst\n\n# Two\n\nsecond\n'], 'a.md')

    const doc = await client.markdown(file, { chunking: { maxTokens: 64, tokenizer: 'word' } })

    assert.deepEqual(doc.options.chunking, { maxTokens: 64, overlap: 0, tokenizer: 'word' })
    assert.deepEqual(
      doc.chunks?.map((chunk) => chunk.index),
      doc.chunks?.map((_, i) => i),
    )
    assert.ok(doc.chunks?.every((chunk) => chunk.kind === 'text'))
  })

  test('markdown through LibreOffice', async () => {
    const doc = await client.markdown(RTF, { filename: 'a.rtf' })

    assert.match(text(doc.result), /Hello from rtf/)
  })

  test('render of a Uint8Array', async () => {
    const page = await client.render(PDF, { page: 2, width: 400, format: 'jpeg' })

    assert.deepEqual([page.result.width, page.result.height, page.result.mimeType], [400, 200, 'image/jpeg'])
    assert.deepEqual(page.options, { page: 2, width: 400, format: 'jpeg' })
    assert.equal(page.source.pageCount, 2)
    assert.deepEqual([...bytes(page.result).subarray(0, 3)], [0xff, 0xd8, 0xff])
    assert.equal(bytes(page.result).length, page.result.size)
    assert.throws(() => text(page.result), TypeError)
  })

  test('thumbnail of an ArrayBuffer', async () => {
    const content = await new Blob([PDF.slice()]).arrayBuffer()

    const thumb = await client.thumbnail(content, { width: 64, height: 64 })

    assert.equal(magic(bytes(thumb.result)), 'RIFF')
    assert.deepEqual(thumb.options, { page: 1, width: 64, height: 64, fit: 'contain', format: 'webp' })
  })

  test('thumbnail through Chromium', async () => {
    const thumb = await client.thumbnail(HTML, { filename: 'a.html', format: 'png' })

    assert.equal(magic(bytes(thumb.result)), '\x89PNG')
  })

  test('inspect of a file on disk', async (t) => {
    const dir = await mkdtemp(join(tmpdir(), 'papyrus-'))
    t.after(() => rm(dir, { recursive: true }))
    const path = join(dir, 'a.pdf')
    await writeFile(path, PDF)

    const { source, checks } = await client.inspect(await openAsBlob(path), { filename: 'a.txt' })

    assert.deepEqual([source.label, source.group, source.size], ['pdf', 'document', PDF.length])
    assert.equal(checks.extensionMatch, false)
  })
})

describe('errors', () => {
  const cases: [string, () => Promise<unknown>, number, string][] = [
    ['a wrong key', () => new Papyrus({ apiKey: 'wrong' }).inspect(CSV), 401, 'unauthorized'],
    ['a page out of range', () => client.render(PDF, { page: 3 }), 422, 'page_out_of_range'],
    ['an invalid option', () => client.render(PDF, { page: 0 }), 422, 'validation_error'],
    ['an unknown option', () => client.render(PDF, { pgae: 1 } as object), 422, 'validation_error'],
    [
      'an option inspect does not have',
      () => client.inspect(CSV, { destination: 's3://a/b' } as object),
      422,
      'validation_error',
    ],
    ['an unknown connector', () => client.markdown('s3://nope/a.pdf'), 422, 'unknown_connector'],
    ['an unsupported format', () => client.markdown(new Uint8Array([0, 1, 2, 3, 255])), 415, 'unsupported_format'],
    ['a private address', () => client.markdown('http://127.0.0.1:8000/'), 403, 'source_forbidden'],
    ['a private page', () => client.screenshot('http://127.0.0.1:8000/'), 403, 'source_forbidden'],
  ]
  for (const [name, call, status, code] of cases) {
    test(name, async () => {
      await assert.rejects(call(), (error) => {
        assert.ok(error instanceof APIError, String(error))
        assert.deepEqual([error.status, error.code], [status, code], String(error))
        assert.ok(error.requestId)
        return true
      })
    })
  }

  test('name the invalid field', async () => {
    await assert.rejects(client.render(PDF, { page: 0 }), (error) => {
      assert.ok(error instanceof APIError)
      assert.deepEqual(error.errors[0]?.loc.slice(-2), ['options', 'page'])
      return true
    })
  })
})

test('probes', async () => {
  assert.deepEqual(await client.health(), { status: 'ok' })
  assert.equal(await client.ready(), true)
})
