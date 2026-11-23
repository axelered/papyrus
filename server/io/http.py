import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import httpx

from server import version
from server.config import HttpConfig
from server.errors import SourceFetchFailed, SourceForbidden, SourceNotFound, SourceTooLarge
from server.io.uri import HTTP_SCHEMES
from server.io.workspace import Workspace
from server.utils.net import HostForbidden, HostPolicy, Resolver, resolve_host
from server.utils.problem import ProblemError

logger = logging.getLogger('papyrus.http')

_CHUNK = 64 * 1024
_NOT_FOUND = frozenset({404, 410})


@dataclass(frozen=True)
class Fetched:
    path: Path
    url: str  # final URL after redirects
    status: int


class HttpFetcher:
    """Downloads http(s) sources, refusing non-public addresses on every hop unless allow_private is set"""

    # todo: the address is resolved again by httpx on connect, so DNS rebinding can bypass the check; pin the IP
    def __init__(
        self,
        config: HttpConfig,
        max_file_size: int,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = resolve_host,
    ):
        self._config = config
        self._max_file_size = max_file_size
        self._client = httpx.AsyncClient(timeout=config.timeout, headers=_headers(), transport=transport)
        self._policy = HostPolicy(config.allowed_hosts, config.allow_private, resolver)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str, workspace: Workspace) -> Fetched:
        if not self._config.enabled:
            raise SourceForbidden('http(s) sources are disabled')
        current = httpx.URL(url)
        try:
            for _ in range(self._config.max_redirects + 1):
                await self._check(current)
                async with self._client.stream('GET', current, follow_redirects=False) as response:
                    if response.is_redirect:
                        current = self._redirect_target(response)
                        continue
                    path = await self._save(response, workspace)
                    return Fetched(path, str(response.url), response.status_code)
        except ProblemError:
            raise
        except httpx.HTTPError as e:
            logger.warning('fetch of %s failed', current, exc_info=e)
            raise SourceFetchFailed(f'fetch of {str(current)!r} failed ({type(e).__name__})') from e
        raise SourceFetchFailed(f'more than {self._config.max_redirects} redirects')

    async def _check(self, url: httpx.URL) -> None:
        if url.scheme not in HTTP_SCHEMES or not url.host:
            raise SourceForbidden(f'{str(url)!r} is not an http(s) URL')
        try:
            await self._policy.check(url.host, url.port or 443)
        except HostForbidden as e:
            raise SourceForbidden(str(e)) from None
        except OSError as e:
            raise SourceFetchFailed(f'cannot resolve host {url.host.lower()!r}') from e

    @staticmethod
    def _redirect_target(response: httpx.Response) -> httpx.URL:
        location = response.headers.get('location')
        if not location:
            raise SourceFetchFailed(f'redirect without location from {str(response.url)!r}')
        return response.url.join(location)

    async def _save(self, response: httpx.Response, workspace: Workspace) -> Path:
        if response.status_code in _NOT_FOUND:
            raise SourceNotFound(f'{str(response.url)!r} returned HTTP {response.status_code}')
        if not response.is_success:
            raise SourceFetchFailed(f'{str(response.url)!r} returned HTTP {response.status_code}')
        length = response.headers.get('content-length')
        if length and length.isdigit() and int(length) > self._max_file_size:
            raise SourceTooLarge(f'source exceeds {self._max_file_size} bytes')

        local = workspace.new_file(_filename(response))
        received = 0
        with local.open('wb') as file:
            async for chunk in response.aiter_bytes(_CHUNK):
                received += len(chunk)
                if received > self._max_file_size:
                    raise SourceTooLarge(f'source exceeds {self._max_file_size} bytes')
                file.write(chunk)
        return local


def _headers() -> dict[str, str]:
    """RFC 9110 product token only (adr/architecture.md, Request Identity)"""
    return {'User-Agent': f'Papyrus/{version.__version__}'}


def _filename(response: httpx.Response) -> str:
    """URL basename, with an extension guessed from Content-Type when missing so format detection still works"""
    name = PurePosixPath(unquote(response.url.path)).name or 'source'
    if not PurePosixPath(name).suffix:
        content_type = response.headers.get('content-type', '').split(';', 1)[0].strip()
        name += mimetypes.guess_extension(content_type) or ''
    return name
