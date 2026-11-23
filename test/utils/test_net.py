import httpx
import pytest

from server.utils.net import (
    HostForbidden,
    HostPolicy,
    HostUnresolved,
    RedirectFailed,
    SafeClient,
    Target,
    is_public,
)

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


# - - - SafeClient


def _client(handler, max_redirects: int = 5, **policy: object) -> SafeClient:
    async def resolver(host: str, _: int) -> list[str]:
        return [PUBLIC]

    transport = httpx.MockTransport(handler)
    return SafeClient(httpx.AsyncClient(transport=transport), HostPolicy(resolver=resolver, **policy), max_redirects)


def _redirect(location: str) -> httpx.Response:
    return httpx.Response(302, headers={'location': location})


async def _get(client: SafeClient, url: str, **kwargs: object) -> httpx.Response:
    async with client.stream(url, **kwargs) as response:
        await response.aread()
        return response


async def test_stream_follows_redirects_and_checks_every_hop():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == 'a.example.com':
            return _redirect('https://b.example.com/doc')
        return httpx.Response(200, content=b'doc')

    client = _client(handler, allowed_hosts=('a.example.com',))

    with pytest.raises(HostForbidden, match='b.example.com'):
        await _get(client, 'https://a.example.com/')
    assert (await _get(_client(handler), 'https://a.example.com/')).content == b'doc'


async def test_stream_rejects_non_http_redirect():
    with pytest.raises(HostForbidden, match='http'):
        await _get(_client(lambda request: _redirect('file:///etc/passwd')), 'https://example.com/')


async def test_stream_limits_redirects():
    with pytest.raises(RedirectFailed, match='more than 2'):
        await _get(_client(lambda request: _redirect('/loop'), max_redirects=2), 'https://example.com/')


async def test_stream_wraps_unresolvable_host():
    async def resolver(host: str, _: int) -> list[str]:
        raise OSError('nxdomain')

    client = SafeClient(httpx.AsyncClient(), HostPolicy(resolver=resolver), 5)

    with pytest.raises(HostUnresolved, match='nowhere.example'):
        await _get(client, 'https://nowhere.example/')


async def test_intercept_replaces_one_hop_with_headers():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f'{request.url} {request.headers.get("authorization")}')
        if request.url.host == 'short.example':
            return _redirect('https://share.example/f/1')
        return httpx.Response(200)

    calls: list[str] = []

    async def intercept(url: httpx.URL) -> Target | None:
        calls.append(str(url))
        if url.host == 'share.example':
            return Target(httpx.URL('https://files.example/1'), {'Authorization': 'Bearer t'})
        return None

    await _get(_client(handler), 'https://short.example/x', intercept=intercept)

    assert calls == ['https://short.example/x', 'https://share.example/f/1']
    assert seen == ['https://short.example/x None', 'https://files.example/1 Bearer t']


async def test_intercept_target_is_policy_checked():
    async def intercept(url: httpx.URL) -> Target | None:
        return Target(httpx.URL('http://127.0.0.1/'))

    client = _client(lambda request: pytest.fail('request must not be sent'))

    with pytest.raises(HostForbidden):
        await _get(client, 'https://example.com/', intercept=intercept)


async def test_redirect_budget_spans_resolution():
    def handler(request: httpx.Request) -> httpx.Response:
        return _redirect('/next') if request.url.host == 'a.example' else _redirect('https://b.example/again')

    async def intercept(url: httpx.URL) -> Target | None:
        return Target(httpx.URL('https://b.example/')) if url.host == 'a.example' else None

    with pytest.raises(RedirectFailed):
        await _get(_client(handler, max_redirects=2), 'https://a.example/', intercept=intercept)


@pytest.mark.parametrize(
    ('location', 'kept'),
    [
        ('/same/origin', True),
        ('https://other.example/', False),
        ('http://example.com/', False),
        ('https://example.com:8443/', False),
    ],
)
async def test_authorization_dropped_on_cross_origin_redirect(location: str, kept: bool):
    final: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/start':
            return _redirect(location)
        final.append(request.headers.get('authorization'))
        return httpx.Response(200)

    await _get(_client(handler), 'https://example.com/start', headers={'Authorization': 'Bearer t'})

    assert final == (['Bearer t'] if kept else [None])
