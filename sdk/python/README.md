# Papyrus Python SDK

Python client for [Papyrus](https://docs.axelered.com/papyrus), the self-hosted API that converts documents and web pages into Markdown, chunks and images for LLMs.

```shell
pip install papyrus-sdk
```

Python 3.11 or later. SDK `X.Y` is made for Papyrus server `X.Y`.

## Usage

```python
from pathlib import Path

from papyrus_sdk import ChunkingOptions, Papyrus

with Papyrus(base_url='http://127.0.0.1:8000', api_key='dev') as client:
    doc = client.markdown(Path('sample.pdf'), chunking=ChunkingOptions(max_tokens=512))
    print(doc.result.text)
    for chunk in doc.chunks or []:
        print(chunk.headings, chunk.tokens)

    page = client.render(Path('sample.pdf'), page=1, format='png')
    page.result.save('page.png')
```

`AsyncPapyrus` has the same methods:

```python
import asyncio

from papyrus_sdk import AsyncPapyrus


async def main() -> None:
    async with AsyncPapyrus(base_url='http://127.0.0.1:8000', api_key='dev') as client:
        doc = await client.markdown(source='https://axelered.com')
        print(doc.result.text)


asyncio.run(main())
```

| Method | Returns |
|---|---|
| `markdown` | Markdown of a document or web page, read from page images with `read_mode='ocr'`, chunked with `chunking` |
| `render` | One page or image frame at a readable size |
| `thumbnail` | One page, image or web page fitted into a box |
| `screenshot` | A web page captured in headless Chromium |
| `inspect` | Detected format and checks of a document |
| `health`, `ready` | Server probes; they do not verify the API key |

Responses are typed models with `source`, `options` and, except for `inspect`, `result`. Read the artifact with `result.data` (bytes), `result.text` (Markdown) or `result.save(path)`. With `destination` the server writes it to storage and only `result.uri` is set.

## Input

| Form | Meaning |
|---|---|
| `client.markdown(x)` | A string with `://` is a URI, any other string is a local path. `Path`, `bytes` and binary files are uploaded |
| `client.markdown(source=x)` | A URI, sent as is |
| `client.markdown(file=x)` | An upload: path, `Path`, `bytes` or binary file |

**Use `source=` for strings that come from users or LLMs.** The positional form reads the local file a string names and sends it to the server; `source=` never reads the local disk.

`AsyncPapyrus` uploads `bytes` only, paths and files raise `NotImplementedError`. `filename=` names an upload sent as `bytes`.

## Configuration

| Argument | Default |
|---|---|
| `base_url` | `PAPYRUS_BASE_URL`, required |
| `api_key` | `PAPYRUS_API_KEY`, no authorization when unset |
| `timeout` | 310 seconds, also per call. Keep it above the server's `limits.deadline` |
| `max_retries` | 2, only for requests the server did not process |
| `http_client` | An `httpx.Client` or `httpx.AsyncClient`, for proxies and private CAs |

## Errors

```python
from papyrus_sdk import APIError, ConnectionFailed

try:
    client.markdown(source='s3://prod/docs/report.pdf')
except APIError as e:
    print(e.status, e.code, e.detail, e.request_id)
except ConnectionFailed:
    print('server unreachable or timed out')
```

Branch on `code`, listed in [error codes](https://docs.axelered.com/papyrus/errors). Every error derives from `PapyrusError`.

## License

Apache License 2.0. Copyright Axelered D.o.o. and Axelered LLC-Fz.
