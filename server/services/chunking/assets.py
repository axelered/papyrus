import logging
import os
import shutil
from pathlib import Path

from server.services.chunking.types import CodeLanguage, Tokenizer

logger = logging.getLogger('papyrus.assets')

ASSETS_ENV = 'PAPYRUS_ASSETS_DIR'
_DEFAULT_ASSETS_DIR = Path(__file__).resolve().parents[3] / '.assets'


def assets_dir() -> Path:
    return Path(os.environ.get(ASSETS_ENV) or _DEFAULT_ASSETS_DIR)


def configure() -> None:
    """Points tree-sitter and tiktoken at the prefetched assets; must run before either library loads anything"""
    import tree_sitter_language_pack as language_pack
    from tree_sitter_language_pack.options import PackConfig

    root = assets_dir()
    os.environ['TIKTOKEN_CACHE_DIR'] = str(root / 'tiktoken')
    language_pack.configure(PackConfig(cache_dir=str(root / 'tree-sitter')))


def available_languages() -> frozenset[CodeLanguage]:
    import tree_sitter_language_pack as language_pack

    downloaded = set(language_pack.downloaded_languages())
    return frozenset(language for language in CodeLanguage if language.value in downloaded)


def prefetch() -> None:
    """Downloads every curated grammar and tiktoken encoding; run at Docker build, idempotent"""
    configure()
    import tiktoken
    import tree_sitter_language_pack as language_pack

    root = assets_dir()
    missing = [language.value for language in CodeLanguage if language not in available_languages()]
    if missing:
        logger.info('downloading %s tree-sitter grammars', len(missing))
        language_pack.download(missing)
    for bundles in (root / 'tree-sitter').rglob('bundles'):
        shutil.rmtree(bundles, ignore_errors=True)
    for tokenizer in Tokenizer:
        if tokenizer.is_tiktoken:
            tiktoken.get_encoding(tokenizer.value)

    absent = set(CodeLanguage) - available_languages()
    if absent:
        raise RuntimeError(f'grammars still missing after prefetch: {sorted(absent)}')
