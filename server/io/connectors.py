from collections.abc import Mapping

import fsspec
from fsspec import AbstractFileSystem

from server.config import ConfigError, ConnectorConfig
from server.errors import UnknownConnector, UnsupportedUri
from server.io.uri import Uri


class Connectors:
    """fsspec filesystems built lazily from YAML connector options"""

    def __init__(self, configs: Mapping[str, ConnectorConfig]):
        self._configs = dict(configs)
        self._filesystems: dict[str, AbstractFileSystem] = {}
        for name, config in self._configs.items():
            try:
                fsspec.get_filesystem_class(config.protocol)
            except (ImportError, ValueError) as e:
                raise ConfigError(f'connector {name!r}: protocol {config.protocol!r} is not available: {e}') from e

    def resolve(self, uri: Uri) -> tuple[AbstractFileSystem, str]:
        config = self._configs.get(uri.authority)
        if config is None:
            raise UnknownConnector(f'connector {uri.authority!r} is not configured')
        if config.protocol != uri.scheme:
            raise UnsupportedUri(f'connector {uri.authority!r} uses {config.protocol}://, not {uri.scheme}://')
        return self._filesystem(uri.authority, config), uri.path

    def _filesystem(self, name: str, config: ConnectorConfig) -> AbstractFileSystem:
        if name not in self._filesystems:
            self._filesystems[name] = fsspec.filesystem(config.protocol, **config.options)
        return self._filesystems[name]
