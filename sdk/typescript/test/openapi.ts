export type Schema = Record<string, unknown>

export interface OpenAPI {
  paths: Record<string, Record<string, unknown>>
  components: { schemas: Record<string, Schema> }
}

/** The document served by the server under test; unset or unreachable fails, a skip would hide drift */
export async function loadOpenAPI(): Promise<OpenAPI> {
  const baseUrl = process.env.PAPYRUS_BASE_URL
  if (!baseUrl) throw new Error('set PAPYRUS_BASE_URL to a running server, e.g. dekit sdk/server')
  const url = `${baseUrl.replace(/\/+$/, '')}/openapi.json`
  const response = await fetch(url).catch((error: unknown) => {
    throw new Error(`server under test at ${baseUrl} is unreachable`, { cause: error })
  })
  if (!response.ok) throw new Error(`GET ${url} answered ${response.status}`)
  return (await response.json()) as OpenAPI
}
