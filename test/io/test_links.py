import httpx
import pytest

from server.errors import SourceForbidden, UnsupportedUri
from server.io.links import LINKS
from server.io.workspace import Workspace
from server.utils.net import SafeClient, Target
from test.fixtures.http import FetcherFactory

pytestmark = pytest.mark.anyio

DRIVE = 'https://drive.usercontent.google.com/download?id=1AbC-_9&export=download&confirm=t'


async def _resolve(url: str) -> tuple[str, str] | None:
    """First matching resolver, as in the fetcher; pure rewrites never use the client"""
    client: SafeClient = None  # type: ignore[assignment]
    for link in LINKS:
        target = await link.resolve(httpx.URL(url), client)
        if target is not None:
            return link.name, str(target.url)
    return None


@pytest.mark.parametrize(
    ('url', 'expected'),
    [
        ('https://drive.google.com/file/d/1AbC-_9/view?usp=sharing', ('google-drive', DRIVE)),
        ('https://drive.google.com/file/d/1AbC-_9', ('google-drive', DRIVE)),
        ('https://drive.google.com/file/u/1/d/1AbC-_9/edit', ('google-drive', DRIVE)),
        ('https://drive.google.com/open?id=1AbC-_9', ('google-drive', DRIVE)),
        ('https://drive.google.com/uc?id=1AbC-_9&export=download', ('google-drive', DRIVE)),
        (
            'https://drive.google.com/file/d/1AbC-_9/view?resourcekey=0-k',
            ('google-drive', f'{DRIVE}&resourcekey=0-k'),
        ),
        (
            'https://docs.google.com/document/d/1Doc_Id/edit?usp=sharing',
            ('google-docs', 'https://docs.google.com/document/d/1Doc_Id/export?format=docx'),
        ),
        (
            'https://docs.google.com/spreadsheets/d/1Sheet/edit#gid=0',
            ('google-docs', 'https://docs.google.com/spreadsheets/d/1Sheet/export?format=xlsx'),
        ),
        (
            'https://docs.google.com/presentation/u/0/d/1Deck/view',
            ('google-docs', 'https://docs.google.com/presentation/d/1Deck/export?format=pptx'),
        ),
        (
            'https://www.dropbox.com/scl/fi/abc/report.pdf?rlkey=k&dl=0',
            ('dropbox', 'https://www.dropbox.com/scl/fi/abc/report.pdf?rlkey=k&dl=1'),
        ),
        ('https://www.dropbox.com/s/abc/report.pdf', ('dropbox', 'https://www.dropbox.com/s/abc/report.pdf?dl=1')),
        (
            'https://axelered-my.sharepoint.com/:w:/g/personal/a/EAbc?e=x1',
            ('onedrive', 'https://axelered-my.sharepoint.com/:w:/g/personal/a/EAbc?e=x1&download=1'),
        ),
        (
            'https://onedrive.live.com/redir?resid=ABC!123&authkey=!k',
            ('onedrive', 'https://onedrive.live.com/download?resid=ABC!123&authkey=!k'),
        ),
        (
            'https://github.com/axelered/papyrus/blob/main/docs/uris.mdx',
            ('github', 'https://raw.githubusercontent.com/axelered/papyrus/main/docs/uris.mdx'),
        ),
    ],
)
async def test_resolves_share_links(url: str, expected: tuple[str, str]):
    assert await _resolve(url) == expected


@pytest.mark.parametrize(
    'url',
    [
        'https://axelered.com/',
        'https://drive.google.com/drive/my-drive',
        'https://docs.google.com/document/d/1Doc/export?format=pdf',
        'https://docs.google.com/document/d/e/2PACX-pub/pub',
        'https://www.dropbox.com/home',
        'https://onedrive.live.com/',
        'https://axelered.sharepoint.com/sites/team',
        'https://1drv.ms/w/s!abc',
        'https://github.com/axelered/papyrus',
        'https://raw.githubusercontent.com/axelered/papyrus/main/README.md',
        'https://drive.google.com.evil.example/file/d/1AbC/view',
        'https://evil.example/drive.google.com/file/d/1AbC/view',
    ],
)
async def test_ignores_other_urls(url: str):
    assert await _resolve(url) is None


@pytest.mark.parametrize(
    'url',
    [
        'https://drive.google.com/drive/folders/1Folder',
        'https://drive.google.com/drive/u/0/folders/1Folder',
        'https://www.dropbox.com/sh/abc/def',
        'https://www.dropbox.com/scl/fo/abc/def?rlkey=k',
        'https://axelered.sharepoint.com/:f:/s/team/EAbc',
        'https://github.com/axelered/papyrus/tree/main/docs',
    ],
)
async def test_rejects_folder_links(url: str):
    with pytest.raises(UnsupportedUri, match='folder'):
        await _resolve(url)


# - - - Fetcher


def _pdf(**headers: str) -> httpx.Response:
    return httpx.Response(200, content=b'%PDF-1.7', headers={'content-type': 'application/pdf', **headers})


async def test_fetch_resolves_share_link(make_fetcher: FetcherFactory, workspace: Workspace):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _pdf(**{'content-disposition': 'attachment; filename="Q3 Report.pdf"'})

    fetcher = make_fetcher(handler, links=LINKS)
    fetched = await fetcher.fetch('https://drive.google.com/file/d/1AbC-_9/view', workspace)

    assert seen == [DRIVE]
    assert (fetched.link, fetched.url, fetched.path.name) == ('google-drive', DRIVE, 'Q3 Report.pdf')


async def test_fetch_resolves_link_behind_redirect(make_fetcher: FetcherFactory, workspace: Workspace):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == 'bit.ly':
            return httpx.Response(301, headers={'location': 'https://drive.google.com/file/d/1AbC-_9/view'})
        if request.url.host == 'drive.usercontent.google.com':
            return httpx.Response(303, headers={'location': 'https://files.googleusercontent.example/blob'})
        return _pdf()

    fetched = await make_fetcher(handler, links=LINKS).fetch('https://bit.ly/q3', workspace)

    assert fetched.link == 'google-drive'
    assert fetched.url == 'https://files.googleusercontent.example/blob'


async def test_fetch_without_links_keeps_url(make_fetcher: FetcherFactory, workspace: Workspace):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b'<html></html>', headers={'content-type': 'text/html'})

    fetched = await make_fetcher(handler).fetch('https://drive.google.com/file/d/1AbC/view', workspace)

    assert seen == ['https://drive.google.com/file/d/1AbC/view']
    assert fetched.link is None


async def test_resolved_host_is_policy_checked(make_fetcher: FetcherFactory, workspace: Workspace):
    fetcher = make_fetcher(lambda request: _pdf(), links=LINKS, allowed_hosts=['drive.google.com'])

    with pytest.raises(SourceForbidden, match='drive.usercontent.google.com'):
        await fetcher.fetch('https://drive.google.com/file/d/1AbC/view', workspace)


async def test_folder_link_fails_before_any_request(make_fetcher: FetcherFactory, workspace: Workspace):
    fetcher = make_fetcher(lambda request: pytest.fail('request must not be sent'), links=LINKS)

    with pytest.raises(UnsupportedUri):
        await fetcher.fetch('https://drive.google.com/drive/folders/1Folder', workspace)


async def test_first_matching_resolver_wins(make_fetcher: FetcherFactory, workspace: Workspace):
    class Always:
        name = 'always'

        async def resolve(self, url: httpx.URL, client: SafeClient) -> Target | None:
            return Target(httpx.URL('https://files.example/a.pdf'))

    fetcher = make_fetcher(lambda request: _pdf(), links=(*LINKS, Always()))
    fetched = await fetcher.fetch('https://github.com/o/r/blob/main/a.pdf', workspace)

    assert (fetched.link, fetched.url) == ('github', 'https://raw.githubusercontent.com/o/r/main/a.pdf')
