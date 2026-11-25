import * as z from 'zod/mini'

// mirrors server/types without bounds and validators, test/schema.test.ts fails when they drift (adr/sdk-typescript.md)

// - - - Enums

export const imageFormat = z.enum(['png', 'jpeg', 'webp'])
export const fit = z.enum(['contain', 'cover'])
export const waitUntil = z.enum(['load', 'domcontentloaded', 'networkidle'])
export const readMode = z.enum(['text', 'ocr'])
export const tokenizer = z.enum(['o200k_base', 'cl100k_base', 'word', 'character'])
export const encoding = z.enum(['utf-8', 'base64'])
export const formatGroup = z.enum([
  'document',
  'spreadsheet',
  'presentation',
  'email',
  'archive',
  'markup',
  'data',
  'code',
  'text',
  'image',
  'unknown',
])
export const chunkKind = z.enum(['text', 'table', 'code'])
export const codeLanguage = z.enum([
  'python',
  'javascript',
  'typescript',
  'tsx',
  'java',
  'kotlin',
  'go',
  'rust',
  'c',
  'cpp',
  'csharp',
  'php',
  'ruby',
  'swift',
  'scala',
  'bash',
  'powershell',
  'sql',
  'yaml',
  'toml',
  'json',
  'html',
  'css',
  'dockerfile',
  'hcl',
])

/** Raster output */
export type ImageFormat = z.infer<typeof imageFormat>
/** How a page fills the thumbnail box */
export type Fit = z.infer<typeof fit>
/** Page event awaited before a screenshot */
export type WaitUntil = z.infer<typeof waitUntil>
/** Where the Markdown comes from: the text stored in the file, or page images read by a vision model */
export type ReadMode = z.infer<typeof readMode>
export type Tokenizer = z.infer<typeof tokenizer>
/** How `result.content` carries the artifact */
export type Encoding = z.infer<typeof encoding>
export type FormatGroup = z.infer<typeof formatGroup>
export type ChunkKind = z.infer<typeof chunkKind>
export type CodeLanguage = z.infer<typeof codeLanguage>

// - - - Files

// absent facts are undefined, the server never sends null
const fileFacts = {
  pageCount: z.optional(z.int()),
  width: z.optional(z.int()),
  height: z.optional(z.int()),
  title: z.optional(z.string()),
}

export const fetchInfo = z.object({
  url: z.string(),
  status: z.optional(z.int()),
})

export const source = z.object({
  uri: z.string(),
  name: z.optional(z.string()),
  size: z.optional(z.int()),
  mimeType: z.optional(z.string()),
  extension: z.optional(z.string()),
  label: z.optional(z.string()),
  group: z.optional(formatGroup),
  ...fileFacts,
  fetch: z.optional(fetchInfo),
})

export const result = z.object({
  uri: z.optional(z.string()),
  name: z.string(),
  size: z.int(),
  mimeType: z.string(),
  extension: z.string(),
  label: z.string(),
  group: formatGroup,
  ...fileFacts,
  encoding: z.optional(encoding),
  content: z.optional(z.string()),
})

/** Final URL after redirects and HTTP status of the document */
export type Fetch = z.infer<typeof fetchInfo>
/** The input document; navigated pages have no file facts. `size` is in bytes, `extension` has no dot */
export type Source = z.infer<typeof source>
/** The primary artifact, read with `text()` or `bytes()`; without content when it was written to a destination */
export type Result = z.infer<typeof result>

// - - - Options

export const chunkingOptions = z.object({
  maxTokens: z.int(),
  overlap: z.int(),
  tokenizer,
})

export const markdownOptions = z.object({
  readMode,
  chunking: z.optional(chunkingOptions),
})

export const thumbnailOptions = z.object({
  page: z.int(),
  width: z.int(),
  height: z.int(),
  fit,
  format: imageFormat,
})

export const renderOptions = z.object({
  page: z.int(),
  width: z.optional(z.int()),
  height: z.optional(z.int()),
  dpi: z.optional(z.int()),
  format: imageFormat,
})

export const screenshotOptions = z.object({
  width: z.int(),
  height: z.int(),
  fullPage: z.boolean(),
  scale: z.int(),
  waitUntil,
  delay: z.int(),
  format: imageFormat,
})

export const inspectOptions = z.object({})

/** Token budget of every chunk, tokens of overlap between prose chunks, and the tokenizer counting them */
export type ChunkingOptions = z.infer<typeof chunkingOptions>
/** `readMode` in a response is the mode used, formats without pages fall back to `text` */
export type MarkdownOptions = z.infer<typeof markdownOptions>
export type ThumbnailOptions = z.infer<typeof thumbnailOptions>
export type RenderOptions = z.infer<typeof renderOptions>
/** `width` and `height` are the viewport, `delay` is in milliseconds */
export type ScreenshotOptions = z.infer<typeof screenshotOptions>
export type InspectOptions = z.infer<typeof inspectOptions>

// - - - Responses

export const inspectChecks = z.object({
  extensionMatch: z.optional(z.boolean()),
})

export const chunk = z.object({
  index: z.int(),
  kind: chunkKind,
  text: z.string(),
  language: z.optional(codeLanguage),
  headings: z.array(z.string()),
  slide: z.optional(z.int()),
  page: z.optional(z.int()),
  start: z.int(),
  end: z.int(),
  tokens: z.int(),
})

export const markdownResponse = z.object({
  source,
  result,
  options: markdownOptions,
  chunks: z.optional(z.array(chunk)),
})

export const thumbnailResponse = z.object({ source, result, options: thumbnailOptions })
export const renderResponse = z.object({ source, result, options: renderOptions })
export const screenshotResponse = z.object({ source, result, options: screenshotOptions })
export const inspectResponse = z.object({ source, options: inspectOptions, checks: inspectChecks })
export const statusResponse = z.object({ status: z.literal('ok') })

export interface Traced {
  /** `X-Request-ID` of the response, to find the request in the server logs */
  requestId?: string
}

/** Every check is true when passed, false when failed and undefined when it does not apply */
export type InspectChecks = z.infer<typeof inspectChecks>
/**
 * `start` and `end` (exclusive) locate the chunk in the Markdown, overlap not included. `slide` is set for pptx and every
 * presentation read with `ocr`, `page` is where the chunk starts in the other documents read with `ocr`
 */
export type Chunk = z.infer<typeof chunk>
/** `chunks` is present with `chunking`, and absent with a destination */
export type MarkdownResponse = z.infer<typeof markdownResponse> & Traced
export type ThumbnailResponse = z.infer<typeof thumbnailResponse> & Traced
export type RenderResponse = z.infer<typeof renderResponse> & Traced
export type ScreenshotResponse = z.infer<typeof screenshotResponse> & Traced
export type InspectResponse = z.infer<typeof inspectResponse> & Traced
export type StatusResponse = z.infer<typeof statusResponse>

// - - - Problems

export const PROBLEM_CODES = [
  'bad_request',
  'deadline_exceeded',
  'destination_failed',
  'http_error',
  'image_too_large',
  'internal_error',
  'method_not_allowed',
  'not_found',
  'not_ready',
  'ocr_failed',
  'ocr_not_configured',
  'ocr_rate_limited',
  'page_out_of_range',
  'payload_too_large',
  'processing_failed',
  'processing_timeout',
  'queue_full',
  'source_fetch_failed',
  'source_forbidden',
  'source_not_found',
  'source_too_large',
  'too_many_pages',
  'unauthorized',
  'unknown_connector',
  'unsupported_format',
  'unsupported_media_type',
  'unsupported_uri',
  'validation_error',
] as const

export const problemIssue = z.object({
  loc: z.array(z.union([z.string(), z.int()])),
  msg: z.string(),
  type: z.string(),
})

export const problem = z.object({
  type: z.optional(z.string()),
  title: z.string(),
  status: z.int(),
  detail: z.nullish(z.string()),
  // open, so that new codes are not rejected
  code: z.string(),
  errors: z.nullish(z.array(problemIssue)),
})

/** Error codes known to this SDK version */
export type ProblemCode = (typeof PROBLEM_CODES)[number]
/** One invalid field of a `validation_error` */
export type ProblemIssue = z.infer<typeof problemIssue>

// - - - Content

/** The artifact as text. Throws TypeError when it is binary or was written to a destination */
export function text(result: Result): string {
  const content = contentOf(result)
  if (result.encoding !== 'utf-8') {
    throw new TypeError(`${result.name} is binary (${result.mimeType}), use bytes()`)
  }
  return content
}

/** The artifact as bytes. Throws TypeError when it was written to a destination */
export function bytes(result: Result): Uint8Array<ArrayBuffer> {
  const content = contentOf(result)
  if (result.encoding !== 'base64') return new TextEncoder().encode(content)
  const binary = atob(content)
  // Uint8Array.from with a callback is 20 times slower
  const data = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) data[i] = binary.charCodeAt(i)
  return data
}

function contentOf(result: Result): string {
  if (result.content === undefined) {
    throw new TypeError(`the result was written to ${result.uri}, the response has no content`)
  }
  return result.content
}
