#!/bin/sh
# Packs the package, installs it in a clean folder and uses it as a consumer would (adr/sdk-typescript.md)
set -eu
cd "$(dirname "$0")/.."
root=$PWD
dir=$(mktemp -d "${TMPDIR:-/tmp}/papyrus-package.XXXXXX")
trap 'rm -rf "$dir"' EXIT

npm pack --silent --pack-destination "$dir" >/dev/null
cd "$dir"
mv ./*.tgz papyrus.tgz
tar -tzf papyrus.tgz | sort
tar -xzf papyrus.tgz
for file in README.md LICENSE NOTICE dist/index.js dist/index.d.ts; do
  test -f "package/$file" || { echo "missing $file" >&2; exit 1; }
done

echo '{"type": "module", "private": true}' >package.json
npm install --silent --no-audit --no-fund ./papyrus.tgz

cat >use.mjs <<'EOF'
import { Papyrus, text, VERSION } from '@axelered/papyrus'
new Papyrus({ baseUrl: 'http://papyrus' })
console.log('import', VERSION, typeof text)
EOF
cat >use.cjs <<'EOF'
const { Papyrus, text, VERSION } = require('@axelered/papyrus')
new Papyrus({ baseUrl: 'http://papyrus' })
console.log('require', VERSION, typeof text)
EOF
cat >use.ts <<'EOF'
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
EOF
# what the package declares as its oldest runtime
docker run --rm -v "$dir":/app:ro -w /app node:22.12-slim sh -c 'node use.mjs && node --no-warnings use.cjs'
bun use.mjs
for module in "nodenext nodenext" "esnext bundler"; do
  # shellcheck disable=SC2086
  set -- $module
  "$root/node_modules/.bin/tsc" --ignoreConfig --noEmit --strict --skipLibCheck false --exactOptionalPropertyTypes \
    --typeRoots "$root/node_modules/@types" --types node --lib es2023 --target es2023 \
    --module "$1" --moduleResolution "$2" use.ts
  echo "types $2"
done
