from collections.abc import Callable

import httpx
import pytest

from server.config import HttpConfig
from server.io.http import HttpFetcher

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
        **config: object,
    ) -> HttpFetcher:
        async def resolver(host: str, _: int) -> list[str]:
            if dns is None:
                return [PUBLIC_IP]
            if host not in dns:
                raise OSError(f'unknown host {host}')
            return dns[host]

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return HttpFetcher(HttpConfig.model_validate(config), max_file_size, client=client, resolver=resolver)

    return make
