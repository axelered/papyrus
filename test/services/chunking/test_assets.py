import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import tiktoken
import tree_sitter_language_pack as language_pack

from server.services.chunking import assets
from server.services.chunking.types import CodeLanguage, Tokenizer


@pytest.fixture
def isolated_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv(assets.ASSETS_ENV, str(tmp_path))
    monkeypatch.setenv('TIKTOKEN_CACHE_DIR', '')
    yield tmp_path
    monkeypatch.undo()
    assets.configure()


def test_configure_points_libraries_at_assets(isolated_assets: Path):
    assets.configure()

    assert os.environ['TIKTOKEN_CACHE_DIR'] == str(isolated_assets / 'tiktoken')
    assert language_pack.downloaded_languages() == []


def test_prefetch_requests_enum_driven_assets(isolated_assets: Path, monkeypatch: pytest.MonkeyPatch):
    downloaded: list[str] = []
    encodings: list[str] = []
    bundles = isolated_assets / 'tree-sitter' / 'v1' / 'bundles'

    def download(names: list[str]) -> int:
        downloaded.extend(names)
        bundles.mkdir(parents=True)
        (bundles / 'all.tar.zst').write_bytes(b'x')
        return len(names)

    monkeypatch.setattr(language_pack, 'download', download)
    monkeypatch.setattr(language_pack, 'downloaded_languages', lambda: list(downloaded))
    monkeypatch.setattr(tiktoken, 'get_encoding', encodings.append)

    assets.prefetch()

    assert downloaded == [language.value for language in CodeLanguage]
    assert encodings == [tokenizer.value for tokenizer in Tokenizer if tokenizer.is_tiktoken]
    assert not bundles.exists()


def test_prefetch_fails_when_grammars_stay_missing(isolated_assets: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(language_pack, 'download', lambda names: 0)
    monkeypatch.setattr(language_pack, 'downloaded_languages', list)
    monkeypatch.setattr(tiktoken, 'get_encoding', lambda name: None)

    with pytest.raises(RuntimeError, match='missing'):
        assets.prefetch()
