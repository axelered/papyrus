import { createServer, type RequestListener } from 'node:http'
import type { AddressInfo, Socket } from 'node:net'
import { createServer as createTcpServer } from 'node:net'
import type { TestContext } from 'node:test'
import { Papyrus, type PapyrusOptions } from '../src/index.ts'

export const URI = 'https://axelered.com'
export const INSPECTED = { source: { uri: 'file://file' }, options: {}, checks: {} }

export function ok(body: unknown = INSPECTED, headers: Record<string, string> = {}): Response {
  return Response.json(body, { headers })
}

export function problem(status: number, code: string, headers: Record<string, string> = {}): Response {
  const body = JSON.stringify({ title: 'Error', status, code, detail: 'why' })
  return new Response(body, { status, headers: { ...headers, 'content-type': 'application/problem+json' } })
}

/** As Node reports it; Bun has the code on the error itself */
export function failure(code: string, runtime: 'node' | 'bun' = 'node'): TypeError {
  const cause = Object.assign(new Error(code), { code })
  return runtime === 'bun' ? Object.assign(new TypeError(code), { code }) : new TypeError('fetch failed', { cause })
}

export interface Sent {
  url: string
  init: RequestInit
  headers: Headers
  text: string
}

/** Fake fetch: replays the outcomes in order, the last one forever, and records the requests */
export class FakeServer {
  readonly sent: Sent[] = []
  readonly #outcomes: readonly (Response | Error)[]

  constructor(...outcomes: (Response | Error)[]) {
    this.#outcomes = outcomes
  }

  readonly fetch = async (url: string, init: RequestInit): Promise<Response> => {
    const request = new Request(url, init)
    this.sent.push({ url, init, headers: request.headers, text: await request.text() })
    const outcome = this.#outcomes[Math.min(this.sent.length, this.#outcomes.length) - 1]
    if (outcome instanceof Response) return outcome.clone()
    throw outcome
  }

  client(options: PapyrusOptions = {}): Papyrus {
    return new Papyrus({ baseUrl: 'http://papyrus', apiKey: 'dev', fetch: this.fetch, ...options })
  }
}

/** Waits between attempts are recorded and skipped; not over real sockets, the runtimes use the same timers */
export function recordSleeps(t: TestContext): number[] {
  const sleeps: number[] = []
  const original = globalThis.setTimeout
  const instant = (callback: () => void, ms = 0): NodeJS.Timeout => {
    sleeps.push(ms)
    return original(callback, 0)
  }
  globalThis.setTimeout = Object.assign(instant, original)
  t.after(() => {
    globalThis.setTimeout = original
  })
  return sleeps
}

export async function listen(t: TestContext, listener: RequestListener): Promise<string> {
  const server = createServer(listener)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  t.after(() => {
    server.closeAllConnections()
    server.close()
  })
  return `http://127.0.0.1:${portOf(server.address())}`
}

/** A server that fails below HTTP */
export async function listenTcp(t: TestContext, onData: (socket: Socket) => void): Promise<string> {
  const sockets: Socket[] = []
  const server = createTcpServer((socket) => {
    sockets.push(socket)
    socket.on('error', () => {})
    socket.once('data', () => onData(socket))
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  t.after(() => {
    for (const socket of sockets) socket.destroy()
    server.close()
  })
  return `http://127.0.0.1:${portOf(server.address())}`
}

function portOf(address: AddressInfo | string | null): number {
  if (address === null || typeof address === 'string') throw new Error('not listening on TCP')
  return address.port
}

export async function closedUrl(): Promise<string> {
  const server = createTcpServer()
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const url = `http://127.0.0.1:${portOf(server.address())}`
  await new Promise((resolve) => server.close(resolve))
  return url
}
