from collections.abc import Callable

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from server.utils.logs import REQUEST_ID_HEADER
from server.utils.timing import SERVER_TIMING_HEADER

# fixed on purpose: auth is a bearer header, never a cookie, so credentials stay off
METHODS = ('GET', 'POST')
ALLOW_HEADERS = ('authorization', 'content-type', REQUEST_ID_HEADER)
EXPOSE_HEADERS = (REQUEST_ID_HEADER, SERVER_TIMING_HEADER, 'content-disposition')


class CorsMiddleware:
    """Starlette CORS for `origins()`, built on the first request because the config loads in the lifespan

    No origins leaves the app untouched: browsers keep the same-origin policy.
    """

    def __init__(self, app: ASGIApp, origins: Callable[[], tuple[str, ...]]):
        self.app = app
        self.origins = origins
        self._inner: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        if self._inner is None:
            self._inner = self._build(self.origins())
        await self._inner(scope, receive, send)

    def _build(self, origins: tuple[str, ...]) -> ASGIApp:
        if not origins:
            return self.app
        return CORSMiddleware(
            self.app,
            allow_origins=origins,
            allow_methods=METHODS,
            allow_headers=ALLOW_HEADERS,
            expose_headers=EXPOSE_HEADERS,
            allow_credentials=False,
        )
