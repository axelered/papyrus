"""The async client: the real app over ASGI, then what it does not share with the sync one"""

import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fsspec import AbstractFileSystem
from papyrus_sdk import APIError, AsyncPapyrus, ConnectionFailed, TimedOut

from test.fixtures.app import API_KEY
from test.fixtures.browser import PNG, FakeBrowser
from test.fixtures.documents import CSV, HTML
from test.fixtures.samples import pdf_pages
from test.fixtures.sdk import BASE_URL, Server, problem

pytestmark = pytest.mark.anyio

URL = 'https://axelered.com'
OK = httpx.Response(200, json={'source': {'uri': URL}, 'options': {}, 'checks': {}})


@asynccontextmanager
async def _connected(app: FastAPI) -> AsyncIterator[AsyncPapyrus]:
    # ASGITransport does not run the lifespan
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app)) as http:
        yield AsyncPapyrus(BASE_URL, API_KEY, http_client=http)


def _papyrus(server: Server, **options: Any) -> AsyncPapyrus:
    http = httpx.AsyncClient(transport=httpx.MockTransport(server), follow_redirects=True)
    return AsyncPapyrus('http://papyrus', 'dev', http_client=http, **options)


# - - - real app


async def test_operations(app: FastAPI, memory_fs: AbstractFileSystem):
    memory_fs.pipe('bucket/in.html', HTML)
    async with _connected(app) as client:
        app.state.services.browser = FakeBrowser()

        markdown = await client.markdown(HTML, filename='report.html')
        stored = await client.markdown('memory://mem/bucket/in.html', destination='memory://mem/bucket/out.md')
        rendered = await client.render(pdf_pages((595, 842)), page=1, width=100, format='jpeg')
        thumbnail = await client.thumbnail(file=pdf_pages((595, 842)), width=64, fit='cover')
        screenshot = await client.screenshot(URL, full_page=True)
        inspected = await client.inspect(source='memory://mem/bucket/in.html')

    assert markdown.result.text.startswith('# Report') and markdown.source.name == 'report.html'
    assert stored.result.uri == 'memory://mem/bucket/out.md' and memory_fs.exists('bucket/out.md')
    assert (rendered.result.width, rendered.result.mime_type) == (100, 'image/jpeg')
    assert thumbnail.result.width == 64
    assert screenshot.result.data == PNG
    assert inspected.source.label == 'html' and inspected.request_id


async def test_problems_become_api_errors(app: FastAPI):
    async with _connected(app) as client:
        with pytest.raises(APIError) as error:
            await client.render(pdf_pages((595, 842)), page=9)

    assert (error.value.status, error.value.code) == (422, 'page_out_of_range')


async def test_probes(app: FastAPI):
    async with _connected(app) as client:
        assert (await client.health()).status == 'ok'
        assert await client.ready() is True

        app.state.admission.in_flight = app.state.admission.limit

        assert await client.ready() is False


# - - - uploads


@pytest.mark.parametrize('form', ['positional', 'file'])
async def test_only_bytes_are_uploaded(tmp_path: Path, form: str):
    path = tmp_path / 'data.csv'
    path.write_bytes(CSV)
    server = Server(OK)
    client = _papyrus(server)

    for upload in (str(path), path, io.BytesIO(CSV), 'missing.csv'):
        with pytest.raises(NotImplementedError, match='bytes'):
            await (client.inspect(upload) if form == 'positional' else client.inspect(file=upload))  # type: ignore[arg-type]

    assert server.requests == []


async def test_invalid_input_is_a_type_error():
    with pytest.raises(TypeError, match='exactly one'):
        await _papyrus(Server(OK)).inspect()
    with pytest.raises(TypeError, match='cannot upload int'):
        await _papyrus(Server(OK)).inspect(file=42)  # type: ignore[arg-type]


# - - - transport


async def test_untouched_requests_are_retried(sleeps: list[float]):
    server = Server(problem(503, 'queue_full'), httpx.ConnectError('refused'), OK)

    await _papyrus(server).inspect(CSV, filename='data.csv')

    assert len(server.requests) == 3 and len(sleeps) == 2
    assert server.requests[0].headers['authorization'] == 'Bearer dev'


async def test_retries_give_up(sleeps: list[float]):
    server = Server(problem(503, 'queue_full'))

    with pytest.raises(APIError):
        await _papyrus(server, max_retries=1).inspect(URL)

    assert len(server.requests) == 2 and len(sleeps) == 1


@pytest.mark.parametrize(
    ('failure', 'kind'),
    [
        (httpx.ReadTimeout('slow'), TimedOut),
        (httpx.ReadError('reset'), ConnectionFailed),
        (problem(504, 'deadline_exceeded'), APIError),
        (httpx.Response(302, headers={'location': 'http://elsewhere/'}), APIError),
    ],
)
async def test_work_is_never_repeated(failure: httpx.Response | Exception, kind: type[Exception]):
    server = Server(failure, OK)

    with pytest.raises(kind):
        await _papyrus(server).inspect(URL)

    assert len(server.requests) == 1


async def test_timeouts():
    server = Server(OK)
    client = _papyrus(server)

    await client.inspect(URL)
    await client.inspect(URL, timeout=2)
    await client.health()

    timeouts = [request.extensions['timeout'] for request in server.requests]
    assert [(t['connect'], t['read']) for t in timeouts] == [(5, 310), (2, 2), (5, 5)]


async def test_only_its_own_http_client_is_closed():
    external = httpx.AsyncClient()
    async with AsyncPapyrus('http://papyrus', http_client=external):
        pass
    async with AsyncPapyrus('http://papyrus') as client:
        owned = client._http

    assert not external.is_closed
    assert owned.is_closed
    await external.aclose()
