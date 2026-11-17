import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SERVER_TIMING_HEADER = 'server-timing'

logger = logging.getLogger('papyrus.timing')

_stages: ContextVar[list[tuple[str, float]] | None] = ContextVar('stages', default=None)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Records the duration of the block, failures included; a no-op outside a timed request

    `name` must be an HTTP token (letters, digits, `.`, `-`, `_`). Nested stages overlap their parent.
    """
    stages = _stages.get()
    if stages is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        stages.append((name, (time.perf_counter() - start) * 1000))


def server_timing(stages: list[tuple[str, float]]) -> str:
    return ', '.join(f'{name};dur={duration:.1f}' for name, duration in stages)


class TimingMiddleware:
    """When `enabled()`, sends stage durations as a Server-Timing header and logs them once per request

    `total` stops at the response start, so a streamed file body is not included. Off by default: the header
    reveals internal timings to clients.
    """

    def __init__(self, app: ASGIApp, enabled: Callable[[], bool]):
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or not self.enabled():
            await self.app(scope, receive, send)
            return

        stages: list[tuple[str, float]] = []
        token = _stages.set(stages)
        start = time.perf_counter()

        async def send_with_timing(message: Message) -> None:
            if message['type'] == 'http.response.start':
                stages.append(('total', (time.perf_counter() - start) * 1000))
                value = server_timing(stages)
                message['headers'] = [*message.get('headers', []), (SERVER_TIMING_HEADER.encode(), value.encode())]
                logger.info('%s %s %s', scope['method'], scope['path'], value)
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            _stages.reset(token)
