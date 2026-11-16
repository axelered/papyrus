import random
from collections.abc import Callable
from pathlib import Path

import pytest

HTML = b'<html><head><title>Quarterly</title></head><body><h1>Report</h1><p>Revenue grew.</p></body></html>'
CSV = b'name,amount\nalpha,1\nbeta,2\n'
# seeded random bytes that no markitdown converter accepts
BINARY = random.Random(0).randbytes(512)

DocumentFactory = Callable[[str, bytes], Path]


@pytest.fixture
def make_document(tmp_path: Path) -> DocumentFactory:
    def make(name: str, content: bytes) -> Path:
        path = tmp_path / 'docs' / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        return path

    return make
