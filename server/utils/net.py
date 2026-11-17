import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

Resolver = Callable[[str, int], Awaitable[list[str]]]


class HostForbidden(PermissionError):
    """The host is not allowed by the policy or resolves to a non-public address"""


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
