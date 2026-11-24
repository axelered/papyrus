import { readFileSync } from 'node:fs'

export type Schema = Record<string, unknown>

export interface OpenAPI {
  paths: Record<string, Record<string, unknown>>
  components: { schemas: Record<string, Schema> }
}

/** The app's document, exported by `bun run openapi`; a missing file fails, a skip would hide drift */
export function loadOpenAPI(): OpenAPI {
  return JSON.parse(readFileSync(new URL('../openapi.json', import.meta.url), 'utf8'))
}
