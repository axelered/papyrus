from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def write_config(tmp_path: Path) -> Callable[[str], Path]:
    def write(content: str) -> Path:
        path = tmp_path / 'config.yml'
        path.write_text(content, encoding='utf-8')
        return path

    return write
