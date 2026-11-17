import asyncio
import ipaddress
import logging
import mimetypes
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import httpx

from server.config import HttpConfig
from server.errors import SourceFetchFailed, SourceForbidden, SourceNotFound, SourceTooLarge
from server.io.uri import HTTP_SCHEMES
from server.io.workspace import Workspace
from server.utils.problem import ProblemError

logger = logging.getLogger('papyrus.http')

Resolver = Callable[[str, int], Awaitable[list[str]]]

_CHUNK = 64 * 1024
_NOT_FOUND = frozenset({404, 410})


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split('%', 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve_host(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class HttpFetcher:
    """Downloads http(s) sources, refusing non-public addresses on every hop unless allow_private is set"""

    # todo: the address is resolved again by httpx on connect, so DNS rebinding can bypass the check; pin the IP
    def __init__(
        self,
        config: HttpConfig,
        max_file_size: int,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver = resolve_host,
    ):
        self._config = config
        self._max_file_size = max_file_size
        self._client = client or httpx.AsyncClient(timeout=config.timeout, headers={'User-Agent': 'papyrus'})
        self._resolver = resolver

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str, workspace: Workspace) -> Path:
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
                    return await self._save(response, workspace)
        except ProblemError:
            raise
        except httpx.HTTPError as e:
            logger.warning('fetch of %s failed', current, exc_info=e)
            raise SourceFetchFailed(f'fetch of {str(current)!r} failed ({type(e).__name__})') from e
        raise SourceFetchFailed(f'more than {self._config.max_redirects} redirects')

    async def _check(self, url: httpx.URL) -> None:
        if url.scheme not in HTTP_SCHEMES or not url.host:
            raise SourceForbidden(f'{str(url)!r} is not an http(s) URL')
        host = url.host.lower()
        if self._config.allowed_hosts and host not in self._config.allowed_hosts:
            raise SourceForbidden(f'host {host!r} is not allowed')
        if self._config.allow_private:
            return
        try:
            addresses = [host] if _is_ip(host) else await self._resolver(host, url.port or 443)
        except OSError as e:
            raise SourceFetchFailed(f'cannot resolve host {host!r}') from e
        if not addresses or not all(is_public(address) for address in addresses):
            raise SourceForbidden(f'host {host!r} resolves to a non-public address')

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


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split('%', 1)[0])
    except ValueError:
        return False
    return True


def _filename(response: httpx.Response) -> str:
    """URL basename, with an extension guessed from Content-Type when missing so format detection still works"""
    name = PurePosixPath(unquote(response.url.path)).name or 'source'
    if not PurePosixPath(name).suffix:
        content_type = response.headers.get('content-type', '').split(';', 1)[0].strip()
        name += mimetypes.guess_extension(content_type) or ''
    return name
