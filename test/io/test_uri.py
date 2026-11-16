import pytest

from server.errors import UnsupportedUri
from server.io.uri import parse_uri


def test_parses_connector_uri_literally():
    uri = parse_uri('s3://prod/bucket/dir/a b%20?#.pdf')

    assert (uri.scheme, uri.authority, uri.path) == ('s3', 'prod', 'bucket/dir/a b%20?#.pdf')
    assert uri.is_connector
    assert uri.filename == 'a b%20?#.pdf'


def test_parses_part_uri():
    uri = parse_uri('file://file')

    assert uri.is_part
    assert uri.authority == 'file'


@pytest.mark.parametrize('raw', ['http://example.com', 'https://example.com/a.pdf?x=1'])
def test_parses_http_uri(raw: str):
    assert parse_uri(raw).is_http


@pytest.mark.parametrize(
    'raw',
    [
        'no-scheme',
        '/local/path.pdf',
        'S3://prod/a',
        '1s3://prod/a',
        's3:///bucket/a',
        's3://prod',
        's3://prod/',
        'file://file/extra',
        'file://',
    ],
)
def test_rejects_invalid_uris(raw: str):
    with pytest.raises(UnsupportedUri):
        parse_uri(raw)
