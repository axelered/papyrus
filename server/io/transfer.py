import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from fsspec import AbstractFileSystem

from server.errors import (
    DestinationFailed,
    SourceFetchFailed,
    SourceNotFound,
    SourceTooLarge,
    UnsupportedUri,
)
from server.io.connectors import Connectors
from server.io.http import Fetched, HttpFetcher
from server.io.uri import Uri, parse_uri
from server.io.workspace import Workspace
from server.utils.problem import ProblemError, ValidationFailed

logger = logging.getLogger('papyrus.transfer')


@dataclass(frozen=True)
class Materialized:
    path: Path
    fetched: Fetched | None = None  # set for http(s) sources


class Transfer:
    """Moves documents between URIs and the request workspace"""

    def __init__(self, connectors: Connectors, http: HttpFetcher, max_file_size: int):
        self._connectors = connectors
        self._http = http
        self._max_file_size = max_file_size

    async def materialize(self, source: str, workspace: Workspace, parts: Mapping[str, Path]) -> Materialized:
        uri = parse_uri(source)
        if uri.is_part:
            if uri.authority not in parts:
                raise ValidationFailed(f'source {source!r} references a missing file part')
            return Materialized(parts[uri.authority])
        if uri.is_http:
            fetched = await self._http.fetch(source, workspace)
            return Materialized(fetched.path, fetched)
        filesystem, remote = self._connectors.resolve(uri)
        local = workspace.new_file(uri.filename)
        await asyncio.to_thread(self._get, filesystem, remote, local)
        return Materialized(local)

    def check_destination(self, destination: str) -> Uri:
        """Validates the destination before any processing happens"""
        uri = parse_uri(destination)
        if not uri.is_connector:
            raise UnsupportedUri(f'destination {destination!r} must be a connector URI')
        self._connectors.resolve(uri)
        return uri

    async def deliver(self, path: Path, destination: str) -> int:
        filesystem, remote = self._connectors.resolve(self.check_destination(destination))
        try:
            await asyncio.to_thread(filesystem.put_file, str(path), remote)
        except Exception as e:
            logger.warning('upload to %s failed', destination, exc_info=e)
            raise DestinationFailed(f'upload to {destination!r} failed ({type(e).__name__})') from e
        return path.stat().st_size

    def _get(self, filesystem: AbstractFileSystem, remote: str, local: Path) -> None:
        try:
            info = filesystem.info(remote)
            if info.get('type') != 'file':
                raise SourceNotFound(f'{remote!r} is not a file')
            if (info.get('size') or 0) > self._max_file_size:
                raise SourceTooLarge(f'source exceeds {self._max_file_size} bytes')
            filesystem.get_file(remote, str(local))
        except ProblemError:
            raise
        except FileNotFoundError as e:
            raise SourceNotFound(f'{remote!r} does not exist') from e
        except Exception as e:
            logger.warning('download of %s failed', remote, exc_info=e)
            raise SourceFetchFailed(f'download of {remote!r} failed ({type(e).__name__})') from e
        if local.stat().st_size > self._max_file_size:
            raise SourceTooLarge(f'source exceeds {self._max_file_size} bytes')
