from collections.abc import Iterator
from contextlib import contextmanager
from typing import Self

from server.errors import ProcessingFailed, ProcessingTimeout
from server.utils.pool import PoolFailed, PoolTimeout


class Service:
    """Lifecycle shared by every service, entered by the registry and closed on shutdown"""

    async def warm(self) -> None:
        """Starts the backend ahead of the first request, at boot with `workers.warmup`; default no-op"""

    async def aclose(self) -> None:
        """Releases pools, browsers and files; default no-op"""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


@contextmanager
def pool_errors() -> Iterator[None]:
    """Maps pool errors to problems; wraps each `ProcessPool.run` of a service"""
    try:
        yield
    except PoolTimeout as e:
        raise ProcessingTimeout(str(e)) from e
    except PoolFailed as e:
        raise ProcessingFailed(str(e)) from e
