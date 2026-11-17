import asyncio
from collections.abc import AsyncIterator

import pytest

from server.utils.proxy import PinningProxy

pytestmark = pytest.mark.anyio

LOOPBACK = '127.0.0.1'


class Upstream:
    """Local server: echoes raw bytes, or answers one http request and records its head"""

    def __init__(self, http: bool):
        self.http = http
        self.heads: list[bytes] = []
        self.connections = 0
        self.server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            if self.http:
                self.heads.append(await reader.readuntil(b'\r\n\r\n'))
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok')
                await writer.drain()
            else:
                while data := await reader.read(1024):
                    writer.write(data)
                    await writer.drain()
        finally:
            writer.close()


async def _serve(http: bool) -> AsyncIterator[Upstream]:
    upstream = Upstream(http)
    upstream.server = await asyncio.start_server(upstream.handle, LOOPBACK, 0)
    yield upstream
    upstream.server.close()


@pytest.fixture
async def echo() -> AsyncIterator[Upstream]:
    async for upstream in _serve(http=False):
        yield upstream


@pytest.fixture
async def web() -> AsyncIterator[Upstream]:
    async for upstream in _serve(http=True):
        yield upstream


class Checks:
    """Pins every `*.test` name to loopback (names that never resolve, so only the pin can connect)"""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> str:
        self.calls.append((host, port))
        if host.startswith('denied'):
            raise PermissionError('denied by test')
        if host.startswith('missing'):
            raise OSError('cannot resolve')
        return LOOPBACK


@pytest.fixture
async def proxy() -> AsyncIterator[tuple[PinningProxy, Checks]]:
    checks = Checks()
    async with PinningProxy(checks) as running:
        yield running, checks


async def _open(proxy: PinningProxy, head: bytes) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection(LOOPBACK, proxy.port)
    writer.write(head)
    await writer.drain()
    return reader, writer


async def _status(reader: asyncio.StreamReader) -> bytes:
    head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
    return head.split(b'\r\n', 1)[0]


async def test_connect_tunnels_to_pinned_address(proxy, echo: Upstream):
    running, checks = proxy
    reader, writer = await _open(running, f'CONNECT site.test:{echo.port} HTTP/1.1\r\nHost: x\r\n\r\n'.encode())

    assert await _status(reader) == b'HTTP/1.1 200 Connection Established'
    writer.write(b'ping')
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(4), 5) == b'ping'
    assert checks.calls == [('site.test', echo.port)]
    writer.close()


async def test_connect_ipv6_authority(proxy):
    running, checks = proxy
    reader, writer = await _open(running, b'CONNECT [::1]:1 HTTP/1.1\r\n\r\n')

    await _status(reader)
    assert checks.calls == [('::1', 1)]
    writer.close()


async def test_http_forward_rewrites_request(proxy, web: Upstream):
    running, _ = proxy
    head = (
        f'GET http://site.test:{web.port}/a/b?q=1 HTTP/1.1\r\nHost: site.test\r\n'
        'Proxy-Connection: keep-alive\r\nProxy-Authorization: Basic eDp5\r\nConnection: keep-alive\r\nX-Kept: 1\r\n\r\n'
    )
    reader, writer = await _open(running, head.encode())

    response = await asyncio.wait_for(reader.read(), 5)

    assert response.startswith(b'HTTP/1.1 200 OK') and response.endswith(b'ok')
    sent = web.heads[0].decode()
    assert sent.startswith('GET /a/b?q=1 HTTP/1.1\r\n')
    assert 'X-Kept: 1' in sent and 'Connection: close' in sent
    assert 'Proxy-' not in sent and 'keep-alive' not in sent
    writer.close()


async def test_http_forward_closes_client_after_response(proxy, web: Upstream):
    running, checks = proxy
    reader, writer = await _open(running, f'GET http://site.test:{web.port}/ HTTP/1.1\r\n\r\n'.encode())

    await asyncio.wait_for(reader.read(), 5)
    assert reader.at_eof()
    assert len(checks.calls) == 1 and web.connections == 1
    writer.close()


@pytest.mark.parametrize(
    ('host', 'status'), [('denied.test', b'HTTP/1.1 403 Forbidden'), ('missing.test', b'HTTP/1.1 502 Bad Gateway')]
)
async def test_check_failures_never_connect(proxy, echo: Upstream, host: str, status: bytes):
    running, _ = proxy
    for head in (f'CONNECT {host}:{echo.port} HTTP/1.1\r\n\r\n', f'GET http://{host}:{echo.port}/ HTTP/1.1\r\n\r\n'):
        reader, writer = await _open(running, head.encode())
        assert await _status(reader) == status
        writer.close()
    assert echo.connections == 0


async def test_refused_upstream_is_bad_gateway(proxy, echo: Upstream):
    running, _ = proxy
    port = echo.port
    assert echo.server is not None
    echo.server.close()
    await echo.server.wait_closed()

    reader, writer = await _open(running, f'CONNECT site.test:{port} HTTP/1.1\r\n\r\n'.encode())

    assert await _status(reader) == b'HTTP/1.1 502 Bad Gateway'
    writer.close()


@pytest.mark.parametrize(
    'head',
    [
        b'garbage\r\n\r\n',
        b'CONNECT site.test HTTP/1.1\r\n\r\n',
        b'CONNECT site.test:99999 HTTP/1.1\r\n\r\n',
        b'GET /relative HTTP/1.1\r\n\r\n',
        b'GET https://site.test/ HTTP/1.1\r\n\r\n',
        b'GET ftp://site.test/ HTTP/1.1\r\n\r\n',
    ],
)
async def test_malformed_requests_are_rejected(proxy, head: bytes):
    running, checks = proxy
    reader, writer = await _open(running, head)

    assert await _status(reader) == b'HTTP/1.1 400 Bad Request'
    assert checks.calls == []
    writer.close()


async def test_oversized_head_is_rejected(proxy):
    running, _ = proxy
    reader, writer = await _open(running, b'GET http://site.test/ HTTP/1.1\r\nX: ' + b'a' * 70_000 + b'\r\n\r\n')

    assert await _status(reader) == b'HTTP/1.1 431 Request Header Fields Too Large'
    writer.close()


async def test_aclose_ends_open_tunnels(echo: Upstream):
    running = PinningProxy(Checks())
    await running.start()
    reader, writer = await _open(running, f'CONNECT site.test:{echo.port} HTTP/1.1\r\n\r\n'.encode())
    await _status(reader)

    await asyncio.wait_for(running.aclose(), 5)

    assert await asyncio.wait_for(reader.read(), 5) == b''
    writer.close()


async def test_on_reject_reports_denials_and_unreachable_hosts(echo: Upstream):
    rejects: list[tuple[str, int, int]] = []
    port = echo.port
    async with PinningProxy(Checks(), on_reject=lambda *reject: rejects.append(reject)) as running:
        for host in ('denied.test', 'missing.test', 'site.test'):
            reader, writer = await _open(running, f'CONNECT {host}:{port} HTTP/1.1\r\n\r\n'.encode())
            await _status(reader)
            writer.close()

    assert rejects == [('denied.test', port, 403), ('missing.test', port, 502)]
