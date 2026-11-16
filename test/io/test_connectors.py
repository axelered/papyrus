import pytest

from server.config import ConfigError, ConnectorConfig
from server.errors import UnknownConnector, UnsupportedUri
from server.io.connectors import Connectors
from server.io.uri import parse_uri


def test_resolves_filesystem_and_path(connectors: Connectors):
    filesystem, path = connectors.resolve(parse_uri('memory://mem/bucket/a.txt'))

    assert type(filesystem).__name__ == 'MemoryFileSystem'
    assert path == 'bucket/a.txt'


def test_caches_filesystem(connectors: Connectors):
    first, _ = connectors.resolve(parse_uri('memory://mem/a'))
    second, _ = connectors.resolve(parse_uri('memory://mem/b'))

    assert first is second


def test_rejects_unknown_connector(connectors: Connectors):
    with pytest.raises(UnknownConnector):
        connectors.resolve(parse_uri('memory://other/a'))


def test_rejects_scheme_protocol_mismatch(connectors: Connectors):
    with pytest.raises(UnsupportedUri, match='memory://'):
        connectors.resolve(parse_uri('s3://mem/a'))


def test_unavailable_protocol_fails_at_startup():
    with pytest.raises(ConfigError, match='nope'):
        Connectors({'x': ConnectorConfig(protocol='nope')})


def test_s3_protocol_is_available():
    Connectors({'prod': ConnectorConfig(protocol='s3', options={'anon': True})})
