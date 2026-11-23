import re
from dataclasses import dataclass

from server.errors import UnsupportedUri
from server.utils.net import HTTP_SCHEMES

PART_SCHEME = 'file'

_SCHEME = re.compile(r'^[a-z][a-z0-9+.-]*$')


@dataclass(frozen=True)
class Uri:
    """`<scheme>://<authority>/<path>`; path is kept literal (no percent-decoding) so object keys round-trip"""

    raw: str
    scheme: str
    authority: str
    path: str

    @property
    def is_part(self) -> bool:
        return self.scheme == PART_SCHEME

    @property
    def is_http(self) -> bool:
        return self.scheme in HTTP_SCHEMES

    @property
    def is_connector(self) -> bool:
        return not self.is_part and not self.is_http

    @property
    def filename(self) -> str:
        return self.path.rsplit('/', 1)[-1]


def parse_uri(raw: str) -> Uri:
    scheme, separator, rest = raw.partition('://')
    if not separator or not _SCHEME.match(scheme):
        raise UnsupportedUri(f'invalid URI {raw!r}, expected <scheme>://<authority>/<path>')
    authority, _, path = rest.partition('/')
    if not authority:
        raise UnsupportedUri(f'URI {raw!r} has no authority')
    uri = Uri(raw=raw, scheme=scheme, authority=authority, path=path)
    if uri.is_part and path:
        raise UnsupportedUri(f'file URI {raw!r} must reference a part name only, as file://<part>')
    if uri.is_connector and not path.strip('/'):
        raise UnsupportedUri(f'connector URI {raw!r} has no path')
    return uri
