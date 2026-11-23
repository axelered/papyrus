"""Share link resolvers: public drive and code host links to the file they share (adr/links.md)"""

import re
from typing import ClassVar, Protocol

import httpx

from server.errors import UnsupportedUri
from server.utils.net import SafeClient, Target


class LinkResolver(Protocol):
    name: str

    async def resolve(self, url: httpx.URL, client: SafeClient) -> Target | None:
        """The request that fetches the shared file, None when the URL is not a link of this provider

        Raises UnsupportedUri for links of this provider that share no single file (folders).
        """
        ...


def _folder(name: str, url: httpx.URL) -> UnsupportedUri:
    return UnsupportedUri(f'{name} link {str(url)!r} is a folder, only links to a single file are supported')


# - - - Google


class GoogleDrive:
    """Uploaded files; the download returns the original bytes, `confirm=t` skips the large file virus scan page"""

    name: ClassVar[str] = 'google-drive'
    _FILE = re.compile(r'^/file(?:/u/\d+)?/d/([\w-]+)')
    _FOLDER = re.compile(r'^/drive(?:/u/\d+)?/folders/')

    async def resolve(self, url: httpx.URL, client: SafeClient) -> Target | None:
        if url.host != 'drive.google.com':
            return None
        if self._FOLDER.match(url.path):
            raise _folder(self.name, url)
        if match := self._FILE.match(url.path):
            file_id = match[1]
        elif url.path in ('/open', '/uc') and url.params.get('id'):
            file_id = url.params['id']
        else:
            return None
        params = {'id': file_id, 'export': 'download', 'confirm': 't'}
        if key := url.params.get('resourcekey'):
            params['resourcekey'] = key
        return Target(httpx.URL('https://drive.usercontent.google.com/download', params=params))


class GoogleDocs:
    """Native Docs, Sheets and Slides have no file; they export to OOXML, lossless for markdown and chunking"""

    name: ClassVar[str] = 'google-docs'
    _DOC = re.compile(r'^/(document|spreadsheets|presentation)(?:/u/\d+)?/d/(?!e/)([\w-]+)(/[^/]*)?')
    _FORMATS: ClassVar[dict[str, str]] = {'document': 'docx', 'spreadsheets': 'xlsx', 'presentation': 'pptx'}

    async def resolve(self, url: httpx.URL, client: SafeClient) -> Target | None:
        if url.host != 'docs.google.com' or not (match := self._DOC.match(url.path)):
            return None
        kind, doc_id, action = match.groups()
        if action == '/export':
            return None  # already an export, keeps the format the user asked for
        return Target(
            httpx.URL(f'https://docs.google.com/{kind}/d/{doc_id}/export', params={'format': self._FORMATS[kind]})
        )


# - - - Dropbox


class Dropbox:
    name: ClassVar[str] = 'dropbox'
    _HOSTS = frozenset({'www.dropbox.com', 'dropbox.com'})

    async def resolve(self, url: httpx.URL, client: SafeClient) -> Target | None:
        if url.host not in self._HOSTS:
            return None
        if url.path.startswith(('/sh/', '/scl/fo/')):
            raise _folder(self.name, url)
        if not url.path.startswith(('/s/', '/scl/fi/')):
            return None
        return Target(url.copy_set_param('dl', '1'))


# - - - Microsoft


class OneDrive:
    """ "Anyone with the link" shares only; `1drv.ms` short links resolve on the hop they redirect to

    `/:<kind>:/` share paths take `download=1`; classic `redir` and `embed` links map to `download`.
    """

    name: ClassVar[str] = 'onedrive'
    _SHARE = re.compile(r'^/:([a-z]):/')
    _CLASSIC = frozenset({'/redir', '/embed'})

    async def resolve(self, url: httpx.URL, client: SafeClient) -> Target | None:
        if url.host != 'onedrive.live.com' and not url.host.endswith('.sharepoint.com'):
            return None
        if share := self._SHARE.match(url.path):
            if share[1] == 'f':
                raise _folder(self.name, url)
            return Target(url.copy_set_param('download', '1'))
        if url.host == 'onedrive.live.com' and url.path in self._CLASSIC:
            return Target(url.copy_with(path='/download'))
        return None


# - - - Code hosts


class GitHub:
    name: ClassVar[str] = 'github'
    _PATH = re.compile(r'^/([^/]+)/([^/]+)/(blob|tree)/(.+)$')

    async def resolve(self, url: httpx.URL, client: SafeClient) -> Target | None:
        if url.host != 'github.com' or not (match := self._PATH.match(url.path)):
            return None
        owner, repo, kind, rest = match.groups()
        if kind == 'tree':
            raise _folder(self.name, url)
        return Target(httpx.URL(f'https://raw.githubusercontent.com/{owner}/{repo}/{rest}'))


LINKS: tuple[LinkResolver, ...] = (GoogleDrive(), GoogleDocs(), Dropbox(), OneDrive(), GitHub())
