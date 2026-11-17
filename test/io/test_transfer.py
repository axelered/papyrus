from pathlib import Path

import httpx
import pytest
from fsspec import AbstractFileSystem

from server.errors import (
    DestinationFailed,
    SourceNotFound,
    SourceTooLarge,
    UnknownConnector,
    UnsupportedUri,
)
from server.io.connectors import Connectors
from server.io.transfer import Transfer
from server.io.workspace import Workspace
from server.utils.problem import ValidationFailed
from test.fixtures.http import FetcherFactory

pytestmark = pytest.mark.anyio


@pytest.fixture
def transfer(connectors: Connectors, make_fetcher: FetcherFactory) -> Transfer:
    fetcher = make_fetcher(lambda request: httpx.Response(200, content=b'remote'), max_file_size=10)
    return Transfer(connectors, fetcher, max_file_size=10)


async def test_materializes_http_source(transfer: Transfer, workspace: Workspace):
    path = await transfer.materialize('https://example.com/a.txt', workspace, {})

    assert path.read_bytes() == b'remote'


async def test_materializes_connector_source(transfer: Transfer, workspace: Workspace, memory_fs: AbstractFileSystem):
    memory_fs.pipe('bucket/dir/doc.html', b'<p>hi</p>')

    path = await transfer.materialize('memory://mem/bucket/dir/doc.html', workspace, {})

    assert path.read_bytes() == b'<p>hi</p>'
    assert path.name == 'doc.html'
    assert path.is_relative_to(workspace.path)


async def test_materializes_part_without_copy(transfer: Transfer, workspace: Workspace, tmp_path: Path):
    part = tmp_path / 'upload.pdf'

    assert await transfer.materialize('file://file', workspace, {'file': part}) is part


async def test_missing_part_is_validation_error(transfer: Transfer, workspace: Workspace):
    with pytest.raises(ValidationFailed):
        await transfer.materialize('file://other', workspace, {})


@pytest.mark.parametrize('remote', ['bucket/missing.txt', 'bucket'])
async def test_missing_or_directory_source_is_not_found(
    transfer: Transfer, workspace: Workspace, memory_fs: AbstractFileSystem, remote: str
):
    memory_fs.pipe('bucket/other.txt', b'x')

    with pytest.raises(SourceNotFound):
        await transfer.materialize(f'memory://mem/{remote}', workspace, {})


async def test_oversized_source_is_rejected(transfer: Transfer, workspace: Workspace, memory_fs: AbstractFileSystem):
    memory_fs.pipe('bucket/big.txt', b'x' * 11)

    with pytest.raises(SourceTooLarge):
        await transfer.materialize('memory://mem/bucket/big.txt', workspace, {})


async def test_unknown_connector_source(transfer: Transfer, workspace: Workspace):
    with pytest.raises(UnknownConnector):
        await transfer.materialize('s3://prod/bucket/a.pdf', workspace, {})


async def test_delivers_to_connector(transfer: Transfer, tmp_path: Path, memory_fs: AbstractFileSystem):
    local = tmp_path / 'out.md'
    local.write_text('# hi')

    size = await transfer.deliver(local, 'memory://mem/out/doc.md')

    assert size == 4
    assert memory_fs.cat('out/doc.md') == b'# hi'


@pytest.mark.parametrize('destination', ['file://file', 'https://example.com/a', 'nope'])
def test_destination_must_be_connector(transfer: Transfer, destination: str):
    with pytest.raises(UnsupportedUri):
        transfer.check_destination(destination)


async def test_delivery_failure_is_wrapped(transfer: Transfer, tmp_path: Path):
    with pytest.raises(DestinationFailed):
        await transfer.deliver(tmp_path / 'absent.md', 'memory://mem/out/doc.md')
