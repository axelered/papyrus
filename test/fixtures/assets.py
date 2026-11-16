import pytest

from server.services.chunking.assets import prefetch


@pytest.fixture(scope='session')
def chunking_assets() -> None:
    """Grammars and encodings in the repo .assets dir; downloads once on a fresh checkout, then offline"""
    prefetch()
