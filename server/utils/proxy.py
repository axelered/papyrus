import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

# resolves and validates a host, returning the address to connect to (the pin)
# raise PermissionError to deny (403), any other exception means the host is unreachable (502)
Check = Callable[[str, int], Awaitable[str]]
# notified with (host, port, status) whenever a request is answered 403 or 502 instead of being connected
OnReject = Callable[[str, int, int], None]

HEAD_LIMIT = 16 * 1024
CONNECT_TIMEOUT = 10
_CHUNK = 64 * 1024
_HOP_HEADERS = frozenset({b'proxy-connection', b'proxy-authorization', b'connection', b'keep-alive'})


class _Reject(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class PinningProxy:
    """HTTP forward proxy that connects only to the address `check` returns, so clients never resolve names

    Supports CONNECT tunnels (https, wss; TLS stays end to end) and absolute-URI http requests. Plain http is one
    request per client connection: the request is sent with `Connection: close` and the client is closed after the
    upstream response, so a reused client connection can never reach a host that was not checked.
    """

    def __init__(self, check: Check, on_reject: OnReject | None = None, logger: logging.Logger | None = None):
        self._check = check
        self._on_reject = on_reject
        self._logger = logger or logging.getLogger(__name__)
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError('proxy is not started')
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._accept, '127.0.0.1', 0)
        return self.port

    async def aclose(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> 'PinningProxy':
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._handle(reader, writer)
        except _Reject as e:
            await _respond(writer, e.status, e.reason)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            if task is not None:
                self._tasks.discard(task)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b'\r\n\r\n')
        except asyncio.LimitOverrunError:
            raise _Reject(431, 'Request Header Fields Too Large') from None
        if len(head) > HEAD_LIMIT:
            raise _Reject(431, 'Request Header Fields Too Large')
        line, _, headers = head.partition(b'\r\n')
        parts = line.split(b' ')
        if len(parts) != 3:
            raise _Reject(400, 'Bad Request')
        method, target, version = parts
        if method == b'CONNECT':
            host, port = _authority(target.decode('latin-1'))
            upstream = await self._connect(host, port)
            await _respond(writer, 200, 'Connection Established', close=False)
            await _relay(reader, writer, *upstream)
            return

        url = urlsplit(target.decode('latin-1'))
        try:
            host, port = url.hostname, url.port or 80
        except ValueError:
            raise _Reject(400, 'Bad Request') from None
        if url.scheme != 'http' or not host:
            raise _Reject(400, 'Bad Request')
        up_reader, up_writer = await self._connect(host, port)
        path = (url.path or '/') + (f'?{url.query}' if url.query else '')
        up_writer.write(b' '.join((method, path.encode('latin-1'), version)) + b'\r\n' + _forward_headers(headers))
        await _relay(reader, writer, up_reader, up_writer)

    async def _connect(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            address = await self._check(host, port)
        except PermissionError as e:
            self._logger.info('proxy denied %s:%s (%s)', host, port, e)
            raise self._reject(host, port, 403, 'Forbidden') from None
        except Exception as e:  # noqa: BLE001 the check owns resolution, any failure means unreachable
            self._logger.info('proxy cannot reach %s:%s (%s)', host, port, e)
            raise self._reject(host, port, 502, 'Bad Gateway') from None
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                return await asyncio.open_connection(address, port)
        except (OSError, TimeoutError) as e:
            self._logger.info('proxy connect to %s (%s:%s) failed (%s)', address, host, port, e)
            raise self._reject(host, port, 502, 'Bad Gateway') from None

    def _reject(self, host: str, port: int, status: int, reason: str) -> _Reject:
        if self._on_reject is not None:
            self._on_reject(host, port, status)
        return _Reject(status, reason)


def _authority(value: str) -> tuple[str, int]:
    """Parses a CONNECT target `host:port` or `[v6]:port`"""
    if value.startswith('['):
        host, _, rest = value[1:].partition(']')
        port = rest.removeprefix(':') if rest.startswith(':') else None
    else:
        host, _, port = value.partition(':')
    if not host or not port or not port.isdigit() or not 0 < int(port) < 65536:
        raise _Reject(400, 'Bad Request')
    return host, int(port)


def _forward_headers(headers: bytes) -> bytes:
    lines = [line for line in headers.split(b'\r\n') if line]
    kept = [line for line in lines if line.split(b':', 1)[0].strip().lower() not in _HOP_HEADERS]
    return b'\r\n'.join([*kept, b'Connection: close']) + b'\r\n\r\n'


async def _respond(writer: asyncio.StreamWriter, status: int, reason: str, close: bool = True) -> None:
    extra = b'Content-Length: 0\r\nConnection: close\r\n' if close else b''
    with contextlib.suppress(ConnectionError):
        writer.write(f'HTTP/1.1 {status} {reason}\r\n'.encode() + extra + b'\r\n')
        await writer.drain()


async def _relay(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
) -> None:
    """Pipes both directions until either side closes, then closes both"""
    tasks = [asyncio.create_task(_pipe(reader, up_writer)), asyncio.create_task(_pipe(up_reader, writer))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        up_writer.close()


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError):
        while data := await source.read(_CHUNK):
            sink.write(data)
            await sink.drain()
