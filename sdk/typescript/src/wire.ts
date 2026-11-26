import type * as z from 'zod/mini'
import { APIError, ConnectionFailed, InvalidResponse, type PapyrusError, TimedOut } from './errors.ts'
import { problem, statusResponse, type Traced } from './models.ts'
import { VERSION } from './version.ts'

/** A document: a URI (`string` or `URL`, sent as is) or the content of a file to upload */
export type Input = string | URL | Blob | ArrayBuffer | Uint8Array

export type FetchFunction = (url: string, init: RequestInit) => Promise<Response>

// the server sends nothing until it is done, so this outlasts its default `limits.deadline` of 300 s
export const DEFAULT_TIMEOUT = 310_000
export const PROBE_TIMEOUT = 5_000
const MAX_WAIT = 30_000
const BACKOFF = 500

// - - - Settings

export interface Settings {
  readonly baseUrl: string
  readonly apiKey: string | undefined
  readonly timeout: number
  readonly maxRetries: number
  readonly fetch: FetchFunction
}

export interface PapyrusOptions {
  /** Defaults to PAPYRUS_BASE_URL */
  baseUrl?: string | undefined
  /** Defaults to PAPYRUS_API_KEY, no authorization is sent when both are unset */
  apiKey?: string | undefined
  /** Milliseconds for one attempt, body included (default 310000). Keep it above the server's `limits.deadline` */
  timeout?: number | undefined
  /** Only requests the server did not process are sent again (default 2) */
  maxRetries?: number | undefined
  /** Replaces the global `fetch`, for proxies, private CAs and longer timeouts on Node */
  fetch?: FetchFunction | undefined
}

export function resolveSettings(options: PapyrusOptions): Settings {
  // absent in browsers
  const env = globalThis.process?.env
  const baseUrl = options.baseUrl || env?.PAPYRUS_BASE_URL
  if (!baseUrl) throw new TypeError('pass baseUrl or set PAPYRUS_BASE_URL')
  if (!/^https?:\/\//i.test(baseUrl) || !URL.canParse(baseUrl)) {
    throw new TypeError('baseUrl must be a URL starting with http:// or https://')
  }
  const { username, password } = new URL(baseUrl)
  if (username || password) throw new TypeError('baseUrl must not contain credentials, fetch refuses them')
  const apiKey = options.apiKey || env?.PAPYRUS_API_KEY || undefined
  if (apiKey !== undefined) checkKey(apiKey)
  const maxRetries = options.maxRetries ?? 2
  if (!Number.isInteger(maxRetries) || maxRetries < 0) {
    throw new RangeError('maxRetries must be an integer, zero or more')
  }
  return {
    baseUrl: baseUrl.replace(/\/+$/, ''),
    apiKey,
    timeout: checkTimeout(options.timeout ?? DEFAULT_TIMEOUT),
    maxRetries,
    // looked up on every call, so that instrumentation installed later is used
    fetch: options.fetch ?? ((url, init) => globalThis.fetch(url, init)),
  }
}

/** Rejected here without echoing the key: the HTTP layer would put an invalid header value in its error */
function checkKey(key: string): void {
  if (key.includes(',')) {
    throw new TypeError('the API key contains a comma: PAPYRUS_API_KEY lists several keys on the server, set one here')
  }
  if (!/^[\x20-\x7e]+$/.test(key) || key !== key.trim()) {
    throw new TypeError('the API key contains whitespace or characters not allowed in a header')
  }
}

export function checkTimeout(timeout: number): number {
  // AbortSignal.timeout takes no more than 32 bits
  if (!(timeout > 0 && timeout <= 0xffff_ffff)) throw new RangeError('timeout is a positive number of milliseconds')
  return timeout
}

export function headersOf({ apiKey }: Settings, call: Call<unknown>): Record<string, string> {
  const headers: Record<string, string> = { accept: 'application/json' }
  if (apiKey !== undefined) headers.authorization = `Bearer ${apiKey}`
  // multipart sets its own, with the boundary
  if (typeof call.body === 'string') headers['content-type'] = 'application/json'
  // not from browsers: it is not a CORS header of the server and would fail the preflight
  if (globalThis.process?.versions?.node) headers['user-agent'] = `papyrus-typescript/${VERSION}`
  return headers
}

// - - - Requests

/** One request, independent of the attempts sending it: strings, forms and blobs can be sent again */
export interface Call<T> {
  readonly method: 'GET' | 'POST'
  readonly path: string
  readonly schema: z.ZodMiniType<T>
  readonly body?: string | FormData
  readonly upload?: Blob
}

export const HEALTH = { method: 'GET', path: '/health', schema: statusResponse } as const
export const READY = { method: 'GET', path: '/ready', schema: statusResponse } as const

export interface OperationOptions {
  filename?: string | undefined
  destination?: string | undefined
  timeout?: number | undefined
  signal?: AbortSignal | undefined
}

/** Every field that is not the client's is an option of the operation, so typos reach the server and are its 422 */
export function operationCall<T>(
  path: string,
  schema: z.ZodMiniType<T>,
  input: Input,
  { filename, destination, timeout: _timeout, signal: _signal, ...options }: OperationOptions,
): Call<T> {
  const body = {
    options,
    // undefined fields are not sent: defaults belong to the server, and inspect has no output
    output: path === '/v1/inspect' ? undefined : 'json',
    destination,
  }
  const target = resolveInput(input, filename)
  if (typeof target === 'string') {
    return { method: 'POST', path, schema, body: JSON.stringify({ source: target, ...body }) }
  }
  const form = new FormData()
  form.set('metadata', JSON.stringify(body))
  form.set('file', target.content, target.filename)
  return { method: 'POST', path, schema, body: form, upload: target.content }
}

interface Upload {
  readonly content: Blob
  readonly filename: string
}

function resolveInput(input: unknown, filename: string | undefined): string | Upload {
  if (typeof input === 'string' || input instanceof URL) {
    if (filename !== undefined) throw new TypeError('filename applies to uploads only')
    return uriOf(input)
  }
  if (input instanceof Blob) return { content: input, filename: filename || nameOf(input) }
  if (isBytes(input)) return { content: new Blob([input]), filename: filename || 'file' }
  throw new TypeError(`cannot send ${describe(input)}, expected a URI, Blob, File, ArrayBuffer or Uint8Array`)
}

export function uriOf(input: unknown): string {
  if (input instanceof URL) return input.href
  if (typeof input !== 'string') throw new TypeError(`expected a URL, got ${describe(input)}`)
  if (!input.includes('://')) {
    throw new TypeError(
      'a string is always a URI and this one has no scheme: to upload a local file pass its content, ' +
        "e.g. await openAsBlob(path) from 'node:fs' or Bun.file(path)",
    )
  }
  return input
}

/** Checked by tag, `instanceof` fails across realms (e.g. Jest) */
function isBytes(value: unknown): value is ArrayBuffer | Uint8Array<ArrayBuffer> {
  return Object.prototype.toString.call(value) === '[object ArrayBuffer]' || ArrayBuffer.isView(value)
}

/** The server refines detection by extension; `Bun.file` has a whole path as name */
function nameOf(blob: Blob): string {
  const name = 'name' in blob && typeof blob.name === 'string' ? blob.name : ''
  return name.split(/[\\/]/).at(-1) || 'file'
}

function describe(value: unknown): string {
  return typeof value === 'object' ? (value?.constructor?.name ?? 'null') : typeof value
}

// - - - Responses

export function parseResponse<T>(call: Call<T>, response: Response, content: string): T & Traced {
  const requestId = response.headers.get('x-request-id') ?? undefined
  if (!response.ok) throw apiError(response, content, requestId)
  const where = `${call.path} did not return a valid response (request ${requestId})`
  let json: unknown
  try {
    json = JSON.parse(content)
  } catch (cause) {
    throw new InvalidResponse(`${where}: not JSON`, { cause })
  }
  const parsed = call.schema.safeParse(json)
  if (!parsed.success) {
    const issues = parsed.error.issues.map((issue) => `${issue.path.join('.')}: ${issue.message}`).join('; ')
    throw new InvalidResponse(`${where}: ${issues}`, { cause: parsed.error })
  }
  const trace: Traced = requestId === undefined ? {} : { requestId }
  return { ...parsed.data, ...trace }
}

function apiError(response: Response, content: string, requestId: string | undefined): APIError {
  const parsed = response.headers.get('content-type')?.startsWith('application/problem+json')
    ? problem.safeParse(tryJson(content))
    : undefined
  return new APIError({
    status: response.status,
    // not a Papyrus problem is reported by status alone
    code: parsed?.data?.code,
    detail: parsed?.data?.detail ?? undefined,
    errors: parsed?.data?.errors ?? undefined,
    requestId,
    retryAfter: retryAfterOf(response.headers.get('retry-after')),
  })
}

function tryJson(content: string): unknown {
  try {
    return JSON.parse(content)
  } catch {
    return undefined
  }
}

function retryAfterOf(value: string | null): number | undefined {
  // the server sends seconds, HTTP dates from proxies fall back to the backoff
  return value !== null && /^\d+$/.test(value) ? Number(value) : undefined
}

// - - - Failures

// the connection was never made, so the server did no work
// Bun on Windows reports a refused connection as `ConnectionRefused`
const NOT_CONNECTED = new Set([
  'ECONNREFUSED',
  'ConnectionRefused',
  'ENOTFOUND',
  'EAI_AGAIN',
  'UND_ERR_CONNECT_TIMEOUT',
])

/** `fetch` only tells failures apart by code: on the error in Bun, on its cause in Node, nowhere in browsers */
function codeOf(error: unknown): string | undefined {
  if (!(error instanceof Error)) return undefined
  if ('code' in error && typeof error.code === 'string') return error.code
  return codeOf(error.cause)
}

export function connectionError(error: unknown, timeout: number | undefined): ConnectionFailed {
  if (timeout !== undefined) return new TimedOut(`no response within ${timeout} ms`, { cause: error })
  const code = codeOf(error)
  if (code === 'UND_ERR_HEADERS_TIMEOUT' || code === 'UND_ERR_BODY_TIMEOUT') {
    return new TimedOut(
      `fetch stopped waiting before the client timeout (${code}): pass a fetch with longer timeouts, ` +
        'see https://docs.axelered.com/papyrus/typescript',
      { cause: error },
    )
  }
  const message = error instanceof Error ? error.message : String(error)
  const text = code === undefined || message.includes(code) ? message : `${message} (${code})`
  return code === 'UND_ERR_CONNECT_TIMEOUT'
    ? new TimedOut(text, { cause: error })
    : new ConnectionFailed(text, { cause: error })
}

/** Only failures where the server did no work, documents are expensive to repeat (adr/sdk-python.md, Retries) */
export function isRetryable(error: PapyrusError): boolean {
  if (error instanceof APIError) {
    return error.code === 'queue_full' || (error.code === undefined && (error.status === 502 || error.status === 503))
  }
  return error instanceof ConnectionFailed && NOT_CONNECTED.has(codeOf(error.cause) ?? '')
}

/** Milliseconds before the next attempt; jittered, the server's `Retry-After` is the same for every client */
export function waitBefore(attempt: number, error: PapyrusError): number {
  const asked = error instanceof APIError ? (error.retryAfter ?? 0) * 1000 : 0
  return Math.min(MAX_WAIT, Math.max(asked, BACKOFF * 2 ** attempt) * (1 + Math.random() / 2))
}

export function sleep(ms: number, signal: AbortSignal | undefined): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason)
    const abort = (): void => {
      clearTimeout(timer)
      reject(signal?.reason)
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', abort)
      resolve()
    }, ms)
    signal?.addEventListener('abort', abort, { once: true })
  })
}
