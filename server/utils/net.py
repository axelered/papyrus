import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx

Resolver = Callable[[str, int], Awaitable[list[str]]]

HTTP_SCHEMES = frozenset({'http', 'https'})


class HostForbidden(PermissionError):
    """The host is not allowed by the policy or resolves to a non-public address"""


class HostUnresolved(Exception):
    """The host has no address"""


class RedirectFailed(Exception):
    """Too many redirects, or a redirect without a location"""


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split('%', 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split('%', 1)[0])
    except ValueError:
        return False
    return True


async def resolve_host(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


@dataclass(frozen=True)
class HostPolicy:
    """Outbound host policy: optional host allowlist, and only public addresses unless allow_private is set"""

    allowed_hosts: tuple[str, ...] = ()
    allow_private: bool = False
    resolver: Resolver = resolve_host

    def check_name(self, host: str) -> str:
        host = host.lower()
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise HostForbidden(f'host {host!r} is not allowed')
        return host

    async def check(self, host: str, port: int) -> None:
        """Validates without pinning; resolution is skipped when private addresses are allowed anyway"""
        host = self.check_name(host)
        if not self.allow_private:
            await self.resolve(host, port)

    async def resolve(self, host: str, port: int) -> str:
        """Returns the address to connect to; every resolved address must pass, so the pin is never a weaker pick

        Raises HostForbidden on policy denial and OSError when the host cannot be resolved.
        """
        host = self.check_name(host)
        addresses = [host.split('%', 1)[0]] if is_ip(host) else await self.resolver(host, port)
        if not addresses:
            raise OSError(f'host {host!r} has no address')
        if not self.allow_private and not all(is_public(address) for address in addresses):
            raise HostForbidden(f'host {host!r} resolves to a non-public address')
        return addresses[0]


@dataclass(frozen=True)
class Target:
    """The request to send instead of a hop, returned by an `intercept` hook"""

    url: httpx.URL
    headers: Mapping[str, str] = field(default_factory=dict)


Intercept = Callable[[httpx.URL], Awaitable[Target | None]]


class SafeClient:
    """httpx GETs that apply the host policy on every hop and follow redirects by hand

    Raises HostForbidden on policy denial, HostUnresolved, RedirectFailed, and httpx errors.
    """

    # todo: the address is resolved again by httpx on connect, so DNS rebinding can bypass the check; pin the IP
    def __init__(self, client: httpx.AsyncClient, policy: HostPolicy, max_redirects: int):
        self._client = client
        self._policy = policy
        self._max_redirects = max_redirects

    async def aclose(self) -> None:
        await self._client.aclose()

    @asynccontextmanager
    async def stream(
        self, url: str | httpx.URL, headers: Mapping[str, str] | None = None, intercept: Intercept | None = None
    ) -> AsyncIterator[httpx.Response]:
        """The first non-redirect response; `intercept` may replace one hop, after which it is no longer called

        `max_redirects` bounds the whole chain. `Authorization` is dropped when a redirect changes origin.
        """
        current = httpx.URL(url)
        sent = httpx.Headers(headers)
        for _ in range(self._max_redirects + 1):
            if intercept is not None and (target := await intercept(current)) is not None:
                current, sent, intercept = target.url, httpx.Headers(target.headers), None
            await self._check(current)
            async with self._client.stream('GET', current, headers=sent, follow_redirects=False) as response:
                if not response.is_redirect:
                    yield response
                    return
                following = _redirect_target(response)
            if _origin(following) != _origin(current):
                sent.pop('authorization', None)
            current = following
        raise RedirectFailed(f'more than {self._max_redirects} redirects')

    async def _check(self, url: httpx.URL) -> None:
        if url.scheme not in HTTP_SCHEMES or not url.host:
            raise HostForbidden(f'{str(url)!r} is not an http(s) URL')
        try:
            await self._policy.check(url.host, url.port or (443 if url.scheme == 'https' else 80))
        except OSError as e:
            if isinstance(e, HostForbidden):
                raise
            raise HostUnresolved(f'cannot resolve host {url.host!r}') from e


def _redirect_target(response: httpx.Response) -> httpx.URL:
    location = response.headers.get('location')
    if not location:
        raise RedirectFailed(f'redirect without location from {str(response.url)!r}')
    return response.url.join(location)


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    return url.scheme, url.host, url.port
