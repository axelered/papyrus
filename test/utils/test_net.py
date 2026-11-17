import pytest

from server.utils.net import HostForbidden, HostPolicy, is_public

pytestmark = pytest.mark.anyio

PUBLIC = '93.184.215.14'


def _policy(dns: dict[str, list[str]], allowed_hosts: tuple[str, ...] = (), allow_private: bool = False) -> HostPolicy:
    async def resolver(host: str, _: int) -> list[str]:
        if host not in dns:
            raise OSError(f'unknown host {host}')
        return dns[host]

    return HostPolicy(allowed_hosts, allow_private, resolver)


@pytest.mark.parametrize(
    ('address', 'public'),
    [
        ('93.184.215.14', True),
        ('2606:4700::1111', True),
        ('127.0.0.1', False),
        ('10.1.2.3', False),
        ('172.16.0.1', False),
        ('192.168.1.1', False),
        ('169.254.169.254', False),
        ('100.64.0.1', False),
        ('0.0.0.0', False),
        ('224.0.0.1', False),
        ('::1', False),
        ('fc00::1', False),
        ('fe80::1%eth0', False),
        ('::ffff:10.0.0.1', False),
        ('::ffff:93.184.215.14', True),
    ],
)
def test_is_public(address: str, public: bool):
    assert is_public(address) is public


async def test_resolve_pins_first_address():
    policy = _policy({'example.com': [PUBLIC, '2606:4700::1111']})

    assert await policy.resolve('Example.COM', 443) == PUBLIC


async def test_resolve_rejects_any_private_address():
    policy = _policy({'mixed.example.com': [PUBLIC, '10.0.0.5']})

    with pytest.raises(HostForbidden, match='non-public'):
        await policy.resolve('mixed.example.com', 443)


async def test_resolve_ip_literal_skips_resolver():
    assert await _policy({}).resolve(PUBLIC, 80) == PUBLIC
    with pytest.raises(HostForbidden):
        await _policy({}).resolve('127.0.0.1', 80)


async def test_resolve_allow_private_still_resolves_for_pinning():
    policy = _policy({'internal': ['10.0.0.5']}, allow_private=True)

    assert await policy.resolve('internal', 80) == '10.0.0.5'


async def test_allowed_hosts_restricts_names():
    policy = _policy({'a.example.com': [PUBLIC]}, allowed_hosts=('a.example.com',))

    assert await policy.resolve('a.example.com', 443) == PUBLIC
    with pytest.raises(HostForbidden, match='not allowed'):
        await policy.resolve('b.example.com', 443)


async def test_unresolvable_host_raises_os_error():
    with pytest.raises(OSError):
        await _policy({}).resolve('missing.example.com', 443)
    with pytest.raises(OSError, match='no address'):
        await _policy({'empty.example.com': []}).resolve('empty.example.com', 443)


async def test_check_skips_resolution_when_private_allowed():
    await _policy({}, allow_private=True).check('missing.example.com', 443)
