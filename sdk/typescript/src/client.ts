import { APIError, PapyrusError } from './errors.ts'
import {
  type ChunkingOptions,
  type InspectResponse,
  inspectResponse,
  type MarkdownOptions,
  type MarkdownResponse,
  markdownResponse,
  type RenderOptions,
  type RenderResponse,
  renderResponse,
  type ScreenshotOptions,
  type ScreenshotResponse,
  type StatusResponse,
  screenshotResponse,
  type ThumbnailOptions,
  type ThumbnailResponse,
  type Traced,
  thumbnailResponse,
} from './models.ts'
import * as wire from './wire.ts'

export interface ProbeParams {
  signal?: AbortSignal | undefined
}

export interface RequestParams extends ProbeParams {
  /** Milliseconds for one attempt, overrides the client's */
  timeout?: number | undefined
}

export interface UploadParams {
  /** Name of an upload; its extension refines detection. Defaults to the name of the `File`, else `file` */
  filename?: string | undefined
}

export interface DestinationParams {
  /** Connector URI the server writes the result to; the response then has no content */
  destination?: string | undefined
}

export type MarkdownParams = Omit<Partial<MarkdownOptions>, 'chunking'> & {
  chunking?: Partial<ChunkingOptions> | undefined
} & UploadParams &
  DestinationParams &
  RequestParams
export type RenderParams = Partial<RenderOptions> & UploadParams & DestinationParams & RequestParams
export type ThumbnailParams = Partial<ThumbnailOptions> & UploadParams & DestinationParams & RequestParams
export type ScreenshotParams = Partial<ScreenshotOptions> & DestinationParams & RequestParams
export type InspectParams = UploadParams & RequestParams

/**
 * Client of the Papyrus API
 *
 * Every operation takes one document: a string or `URL` is always a URI, a `Blob`, `File`, `ArrayBuffer` or
 * `Uint8Array` is uploaded. Local paths are never read. Options left out take the server defaults.
 */
export class Papyrus {
  // private at run time too, the key must not show up in logs
  readonly #settings: wire.Settings

  constructor(options: wire.PapyrusOptions = {}) {
    this.#settings = wire.resolveSettings(options)
  }

  /** Markdown of a document or web page, read from page images with `readMode`, chunked with `chunking` */
  async markdown(input: wire.Input, params: MarkdownParams = {}): Promise<MarkdownResponse> {
    return this.#send(wire.operationCall('/v1/markdown', markdownResponse, input, params), params)
  }

  /** One page or image frame at a readable size */
  async render(input: wire.Input, params: RenderParams = {}): Promise<RenderResponse> {
    return this.#send(wire.operationCall('/v1/render', renderResponse, input, params), params)
  }

  /** One page, image or web page fitted into a box */
  async thumbnail(input: wire.Input, params: ThumbnailParams = {}): Promise<ThumbnailResponse> {
    return this.#send(wire.operationCall('/v1/thumbnail', thumbnailResponse, input, params), params)
  }

  /** A web page captured in headless Chromium */
  async screenshot(url: string | URL, params: ScreenshotParams = {}): Promise<ScreenshotResponse> {
    return this.#send(wire.operationCall('/v1/screenshot', screenshotResponse, wire.uriOf(url), params), params)
  }

  /** Detected format and checks of a document, without processing it */
  async inspect(input: wire.Input, params: InspectParams = {}): Promise<InspectResponse> {
    return this.#send(wire.operationCall('/v1/inspect', inspectResponse, input, params), params)
  }

  /** Rejects unless the server is up; a public route, the API key is not verified */
  async health({ signal }: ProbeParams = {}): Promise<StatusResponse> {
    const { status } = await this.#attempt(wire.HEALTH, wire.PROBE_TIMEOUT, signal)
    return { status }
  }

  /** False when the server is at capacity, rejects when it is unreachable; the API key is not verified */
  async ready({ signal }: ProbeParams = {}): Promise<boolean> {
    try {
      await this.#attempt(wire.READY, wire.PROBE_TIMEOUT, signal)
      return true
    } catch (error) {
      if (error instanceof APIError && error.code === 'not_ready') return false
      throw error
    }
  }

  async #send<T>(call: wire.Call<T>, { timeout, signal }: RequestParams): Promise<T & Traced> {
    const limit = timeout === undefined ? this.#settings.timeout : wire.checkTimeout(timeout)
    for (let attempt = 0; ; attempt++) {
      try {
        return await this.#attempt(call, limit, signal)
      } catch (error) {
        const retry = error instanceof PapyrusError && wire.isRetryable(error)
        if (!retry || attempt >= this.#settings.maxRetries) throw error
        await wire.sleep(wire.waitBefore(attempt, error), signal)
      }
    }
  }

  async #attempt<T>(call: wire.Call<T>, timeout: number, signal: AbortSignal | undefined): Promise<T & Traced> {
    signal?.throwIfAborted()
    // a file deleted or changed since it was opened rejects here: inside fetch it would crash the Node process
    await call.upload?.slice(0, 1).arrayBuffer()
    const settings = this.#settings
    const expiry = AbortSignal.timeout(timeout)
    let response: Response
    let content: string
    try {
      response = await settings.fetch(settings.baseUrl + call.path, {
        method: call.method,
        headers: wire.headersOf(settings, call),
        body: call.body ?? null,
        redirect: 'manual',
        signal: signal ? AbortSignal.any([signal, expiry]) : expiry,
      })
      content = await response.text()
    } catch (error) {
      // the caller's own abort is not a failure of the request
      if (signal?.aborted) throw signal.reason
      throw wire.connectionError(error, expiry.aborted ? timeout : undefined)
    }
    return wire.parseResponse(call, response, content)
  }
}
