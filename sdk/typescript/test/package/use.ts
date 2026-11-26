import { APIError, bytes, type ImageFormat, type MarkdownResponse, Papyrus, text } from '@axelered/papyrus'

const client = new Papyrus({ baseUrl: 'http://papyrus', fetch: (url, init) => fetch(url, init) })
const format: ImageFormat = 'png'

export async function use(signal: AbortSignal): Promise<Uint8Array> {
  const doc: MarkdownResponse = await client.markdown(Buffer.from('a'), { chunking: { maxTokens: 64 }, signal })
  const language: 'python' | 'sql' | undefined | string = doc.chunks?.[0]?.language
  console.log(text(doc.result), language)
  // @ts-expect-error: not a format
  await client.render('https://axelered.com', { format: 'bmp' })
  // @ts-expect-error: not an option
  await client.render('https://axelered.com', { pgae: 1 })
  // @ts-expect-error: inspect has no destination
  await client.inspect('https://axelered.com', { destination: 's3://prod/a' })
  // @ts-expect-error: pages are URLs
  await client.screenshot(new Blob([]))
  try {
    return bytes((await client.render(new Uint8Array(), { page: 1, format })).result)
  } catch (error) {
    if (error instanceof APIError && error.code === 'queue_full') throw new Error(error.requestId)
    throw error
  }
}
