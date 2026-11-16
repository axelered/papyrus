from collections.abc import Iterator
from pathlib import Path

import fsspec
import pytest
from fsspec import AbstractFileSystem

from server.config import ConnectorConfig
from server.io.connectors import Connectors
from server.io.workspace import Workspace

MEMORY_CONNECTORS = {'mem': ConnectorConfig(protocol='memory')}


@pytest.fixture
def memory_fs() -> Iterator[AbstractFileSystem]:
    """fsspec memory filesystem shared by all memory connectors, emptied around each test"""
    filesystem = fsspec.filesystem('memory')
    filesystem.store.clear()
    filesystem.pseudo_dirs.clear()
    filesystem.pseudo_dirs.append('')
    yield filesystem
    filesystem.store.clear()


@pytest.fixture
def connectors(memory_fs: AbstractFileSystem) -> Connectors:
    return Connectors(MEMORY_CONNECTORS)


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Workspace]:
    with Workspace(tmp_path / 'work') as workspace:
        yield workspace
