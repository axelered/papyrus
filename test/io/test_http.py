import httpx
import pytest

from server.errors import SourceFetchFailed, SourceForbidden, SourceNotFound, SourceTooLarge
from server.io.http import is_public
from server.io.workspace import Workspace
from test.fixtures.http import FetcherFactory

pytestmark = pytest.mark.anyio


def _ok(content: bytes = b'data', **headers: str) -> httpx.Response:
    return httpx.Response(200, content=content, headers=headers)


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


async def test_fetches_into_workspace(make_fetcher: FetcherFactory, workspace: Workspace):
    fetcher = make_fetcher(lambda request: _ok(b'%PDF-1.7'))

    path = await fetcher.fetch('https://example.com/files/My%20Report.pdf?x=1', workspace)

    assert path.read_bytes() == b'%PDF-1.7'
    assert path.name == 'My Report.pdf'
    assert path.is_relative_to(workspace.path)


async def test_guesses_extension_from_content_type(make_fetcher: FetcherFactory, workspace: Workspace):
    fetcher = make_fetcher(lambda request: _ok(**{'content-type': 'application/pdf; charset=binary'}))

    assert (await fetcher.fetch('https://example.com/download', workspace)).name == 'download.pdf'
    assert (await fetcher.fetch('https://example.com/', workspace)).name == 'source.pdf'


@pytest.mark.parametrize(
    'url', ['http://127.0.0.1/a', 'http://169.254.169.254/latest/meta-data', 'http://[::1]/a', 'http://10.0.0.1:8080/']
)
async def test_blocks_private_ip_literals(make_fetcher: FetcherFactory, workspace: Workspace, url: str):
    fetcher = make_fetcher(lambda request: pytest.fail('request must not be sent'))

    with pytest.raises(SourceForbidden):
        await fetcher.fetch(url, workspace)


async def test_blocks_host_resolving_to_any_private_address(make_fetcher: FetcherFactory, workspace: Workspace):
    fetcher = make_fetcher(lambda request: _ok(), dns={'internal.example.com': ['93.184.215.14', '10.0.0.5']})

    with pytest.raises(SourceForbidden, match='non-public'):
        await fetcher.fetch('https://internal.example.com/a', workspace)


async def test_allow_private_skips_address_check(make_fetcher: FetcherFactory, workspace: Workspace):
    fetcher = make_fetcher(lambda request: _ok(), dns={}, allow_private=True)

    assert (await fetcher.fetch('http://127.0.0.1/a.txt', workspace)).read_bytes() == b'data'


async def test_allowed_hosts_restricts_targets(make_fetcher: FetcherFactory, workspace: Workspace):
    fetcher = make_fetcher(lambda request: _ok(), allowed_hosts=['Docs.Example.com'])

    assert await fetcher.fetch('https://docs.example.com/a', workspace)
    with pytest.raises(SourceForbidden, match='not allowed'):
        await fetcher.fetch('https://other.example.com/a', workspace)


async def test_disabled_http_is_forbidden(make_fetcher: FetcherFactory, workspace: Workspace):
    fetcher = make_fetcher(lambda request: _ok(), enabled=False)

    with pytest.raises(SourceForbidden, match='disabled'):
        await fetcher.fetch('https://example.com/a', workspace)


async def test_follows_relative_redirects(make_fetcher: FetcherFactory, workspace: Workspace):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/old':
            return httpx.Response(302, headers={'location': '/new/doc.txt'})
        return _ok(b'moved')

    path = await make_fetcher(handler).fetch('https://example.com/old', workspace)

    assert path.read_bytes() == b'moved'
    assert path.name == 'doc.txt'


async def test_revalidates_redirect_target(make_fetcher: FetcherFactory, workspace: Workspace):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == 'example.com':
            return httpx.Response(301, headers={'location': 'http://169.254.169.254/latest'})
        return pytest.fail('redirect target must not be requested')

    with pytest.raises(SourceForbidden):
        await make_fetcher(handler).fetch('https://example.com/a', workspace)


async def test_limits_redirects(make_fetcher: FetcherFactory, workspace: Workspace):
    fetcher = make_fetcher(lambda request: httpx.Response(302, headers={'location': '/loop'}), max_redirects=2)

    with pytest.raises(SourceFetchFailed, match='redirects'):
        await fetcher.fetch('https://example.com/loop', workspace)


async def test_redirect_without_location_fails(make_fetcher: FetcherFactory, workspace: Workspace):
    with pytest.raises(SourceFetchFailed, match='location'):
        await make_fetcher(lambda request: httpx.Response(302)).fetch('https://example.com/a', workspace)


@pytest.mark.parametrize(('status', 'error'), [(404, SourceNotFound), (410, SourceNotFound), (500, SourceFetchFailed)])
async def test_maps_error_status(make_fetcher: FetcherFactory, workspace: Workspace, status: int, error: type):
    with pytest.raises(error):
        await make_fetcher(lambda request: httpx.Response(status)).fetch('https://example.com/a', workspace)


async def test_rejects_declared_oversize(make_fetcher: FetcherFactory, workspace: Workspace):
    fetcher = make_fetcher(lambda request: _ok(b'x' * 4, **{'content-length': '5000'}), max_file_size=10)

    with pytest.raises(SourceTooLarge):
        await fetcher.fetch('https://example.com/a', workspace)


async def test_rejects_streamed_oversize(make_fetcher: FetcherFactory, workspace: Workspace):
    async def chunks():
        yield b'x' * 8
        yield b'x' * 8

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunks())

    with pytest.raises(SourceTooLarge):
        await make_fetcher(handler, max_file_size=10).fetch('https://example.com/a', workspace)


async def test_wraps_transport_errors(make_fetcher: FetcherFactory, workspace: Workspace):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('refused')

    with pytest.raises(SourceFetchFailed, match='ConnectError'):
        await make_fetcher(handler).fetch('https://example.com/a', workspace)


async def test_wraps_dns_failure(make_fetcher: FetcherFactory, workspace: Workspace):
    with pytest.raises(SourceFetchFailed, match='resolve'):
        await make_fetcher(lambda request: _ok(), dns={}).fetch('https://nowhere.example/a', workspace)
