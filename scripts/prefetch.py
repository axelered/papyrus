import logging
import shutil

from server.services import chunking
from server.types.markdown import CodeLanguage, Tokenizer

logger = logging.getLogger('papyrus.prefetch')


def prefetch() -> None:
    """Downloads every curated grammar and tiktoken encoding; run at Docker build, idempotent"""
    chunking.configure()
    import tiktoken
    import tree_sitter_language_pack as language_pack

    root = chunking.assets_dir()
    missing = [language.value for language in CodeLanguage if language not in chunking.available_languages()]
    if missing:
        logger.info('downloading %s tree-sitter grammars', len(missing))
        language_pack.download(missing)
    for bundles in (root / 'tree-sitter').rglob('bundles'):
        shutil.rmtree(bundles, ignore_errors=True)
    for tokenizer in Tokenizer:
        if tokenizer.is_tiktoken:
            tiktoken.get_encoding(tokenizer.value)

    absent = set(CodeLanguage) - chunking.available_languages()
    if absent:
        raise RuntimeError(f'grammars still missing after prefetch: {sorted(absent)}')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    prefetch()
    print(f'assets ready in {chunking.assets_dir()}')
