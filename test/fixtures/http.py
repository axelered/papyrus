from collections.abc import Callable

import httpx
import pytest

from server.config import HttpConfig
from server.io.http import HttpFetcher
from server.io.links import LinkResolver

Handler = Callable[[httpx.Request], httpx.Response]
FetcherFactory = Callable[..., HttpFetcher]

PUBLIC_IP = '93.184.215.14'


@pytest.fixture
def make_fetcher() -> FetcherFactory:
    """Builds a fetcher over a mock transport; hosts resolve via `dns` (default: every host is public)"""

    def make(
        handler: Handler,
        dns: dict[str, list[str]] | None = None,
        max_file_size: int = 1024,
        links: tuple[LinkResolver, ...] = (),
        **config: object,
    ) -> HttpFetcher:
        async def resolver(host: str, _: int) -> list[str]:
            if dns is None:
                return [PUBLIC_IP]
            if host not in dns:
                raise OSError(f'unknown host {host}')
            return dns[host]

        return HttpFetcher(
            HttpConfig.model_validate(config),
            max_file_size,
            transport=httpx.MockTransport(handler),
            resolver=resolver,
            links=links,
        )

    return make
