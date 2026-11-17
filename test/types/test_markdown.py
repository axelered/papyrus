from typing import get_args

import pytest
from pydantic import ValidationError
from tree_sitter_language_pack import SupportedLanguage

from server.types.common import OutputMode
from server.types.markdown import ChunkingOptions, CodeLanguage, MarkdownOptions, MarkdownRequest, Tokenizer


def test_code_languages_are_tree_sitter_names():
    assert {language.value for language in CodeLanguage} <= set(get_args(SupportedLanguage))


def test_tiktoken_tokenizers():
    assert {tokenizer for tokenizer in Tokenizer if tokenizer.is_tiktoken} == {
        Tokenizer.O200K_BASE,
        Tokenizer.CL100K_BASE,
    }


def test_overlap_must_stay_below_budget():
    with pytest.raises(ValidationError, match='overlap'):
        ChunkingOptions(max_tokens=64, overlap=64)


def test_chunking_requires_json_output():
    options = MarkdownOptions(chunking=ChunkingOptions())

    with pytest.raises(ValidationError, match='output=json'):
        MarkdownRequest(source='s3://c/a.pdf', output=OutputMode.FILE, options=options)
