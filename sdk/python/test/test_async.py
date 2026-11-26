"""The async client: the server under test, then what it does not share with the sync one"""

import io
from pathlib import Path
from typing import Any

import httpx
import pytest
from papyrus_sdk import APIError, AsyncPapyrus, ConnectionFailed, TimedOut
from support import CSV, HTML, JPEG, WEBP, Server, pdf, problem

pytestmark = pytest.mark.anyio

URL = 'https://axelered.com'
OK = httpx.Response(200, json={'source': {'uri': URL}, 'options': {}, 'checks': {}})
PAGE = (200, 100)


def _papyrus(server: Server, **options: Any) -> AsyncPapyrus:
    http = httpx.AsyncClient(transport=httpx.MockTransport(server), follow_redirects=True)
    return AsyncPapyrus('http://papyrus', 'dev', http_client=http, **options)


# - - - server under test


async def test_operations(async_sdk: AsyncPapyrus):
    markdown = await async_sdk.markdown(HTML, filename='rapporto è.html')
    rendered = await async_sdk.render(pdf(PAGE), page=1, width=100, format='jpeg')
    thumbnail = await async_sdk.thumbnail(file=pdf(PAGE), width=64, fit='cover')
    inspected = await async_sdk.inspect(CSV, filename='data.csv')

    assert markdown.result.text.startswith('# Report') and markdown.source.name == 'rapporto è.html'
    assert (rendered.result.width, rendered.result.mime_type) == (100, 'image/jpeg')
    assert rendered.result.data.startswith(JPEG)
    assert thumbnail.result.width == 64 and thumbnail.result.data.startswith(WEBP)
    assert inspected.source.label == 'csv' and inspected.request_id


async def test_problems_become_api_errors(async_sdk: AsyncPapyrus):
    with pytest.raises(APIError) as out_of_range:
        await async_sdk.render(pdf(PAGE), page=9)
    with pytest.raises(APIError) as private:
        await async_sdk.screenshot('http://127.0.0.1:8000/')

    assert (out_of_range.value.status, out_of_range.value.code) == (422, 'page_out_of_range')
    assert (private.value.status, private.value.code) == (403, 'source_forbidden')


async def test_probes(async_sdk: AsyncPapyrus):
    assert (await async_sdk.health()).status == 'ok'
    assert await async_sdk.ready() is True


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
