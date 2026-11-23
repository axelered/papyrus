import logging
import mimetypes
from dataclasses import dataclass
from email.message import Message
from email.utils import collapse_rfc2231_value
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import httpx

from server import version
from server.config import HttpConfig
from server.errors import SourceFetchFailed, SourceForbidden, SourceNotFound, SourceTooLarge
from server.io.workspace import Workspace
from server.utils.net import (
    HostForbidden,
    HostPolicy,
    HostUnresolved,
    RedirectFailed,
    Resolver,
    SafeClient,
    resolve_host,
)
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

    def __init__(
        self,
        config: HttpConfig,
        max_file_size: int,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = resolve_host,
    ):
        self._config = config
        self._max_file_size = max_file_size
        self._client = SafeClient(
            httpx.AsyncClient(timeout=config.timeout, headers=_headers(config.locale), transport=transport),
            HostPolicy(config.allowed_hosts, config.allow_private, resolver),
            config.max_redirects,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str, workspace: Workspace) -> Fetched:
        if not self._config.enabled:
            raise SourceForbidden('http(s) sources are disabled')
        try:
            async with self._client.stream(url) as response:
                path = await self._save(response, workspace)
                return Fetched(path, str(response.url), response.status_code)
        except ProblemError:
            raise
        except HostForbidden as e:
            raise SourceForbidden(str(e)) from None
        except RedirectFailed as e:
            raise SourceFetchFailed(str(e)) from None
        except HostUnresolved as e:
            raise SourceFetchFailed(str(e)) from e
        except httpx.HTTPError as e:
            logger.warning('fetch of %s failed', url, exc_info=e)
            raise SourceFetchFailed(f'fetch of {url!r} failed ({type(e).__name__})') from e

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


def _headers(locale: str | None) -> dict[str, str]:
    """RFC 9110 product token only, and the configured language (adr/architecture.md, Request Identity)"""
    headers = {'User-Agent': f'Papyrus/{version.__version__}'}
    if locale:
        headers['Accept-Language'] = accept_language(locale)
    return headers


def accept_language(locale: str) -> str:
    """The tag, then its base language as a fallback, as Chromium sends it: `it-IT` gives `it-IT,it;q=0.9`"""
    base = locale.split('-', 1)[0]
    return locale if base == locale else f'{locale},{base};q=0.9'


def _filename(response: httpx.Response) -> str:
    """`Content-Disposition` name, else the URL basename, with an extension guessed from Content-Type when missing"""
    name = _disposition_name(response.headers.get('content-disposition'))
    name = name or PurePosixPath(unquote(response.url.path)).name or 'source'
    if not PurePosixPath(name).suffix:
        content_type = response.headers.get('content-type', '').split(';', 1)[0].strip()
        name += mimetypes.guess_extension(content_type) or ''
    return name


def _disposition_name(header: str | None) -> str | None:
    """RFC 6266 filename, `filename*` before `filename`; the workspace strips any path from it"""
    if not header:
        return None
    message = Message()
    message['content-disposition'] = header
    names = [value for key, value in message.get_params([], header='content-disposition') if key == 'filename']
    extended = [collapse_rfc2231_value(value) for value in names if isinstance(value, tuple)]
    plain = [value for value in names if isinstance(value, str)]
    return next((name for name in [*extended, *plain] if name.strip()), None)
