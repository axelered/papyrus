from typing import get_args

import pytest
from tree_sitter_language_pack import SupportedLanguage

from server.formats import lookup
from server.services.chunking.types import LANGUAGE_ALIASES, CodeLanguage, Tokenizer, resolve_language


def test_code_languages_are_tree_sitter_names():
    assert {language.value for language in CodeLanguage} <= set(get_args(SupportedLanguage))


def test_aliases_never_shadow_language_values():
    assert set(LANGUAGE_ALIASES).isdisjoint(language.value for language in CodeLanguage)


@pytest.mark.parametrize(
    ('tag', 'language'),
    [
        ('python', CodeLanguage.PYTHON),
        (' Py ', CodeLanguage.PYTHON),
        ('cs', CodeLanguage.CSHARP),
        ('shell', CodeLanguage.BASH),
        ('tf', CodeLanguage.HCL),
        ('elixir', None),
        ('', None),
        (None, None),
    ],
)
def test_resolve_language(tag: str | None, language: CodeLanguage | None):
    assert resolve_language(tag) is language


@pytest.mark.parametrize('label', ['python', 'javascript', 'typescript', 'cs', 'shell', 'go', 'rust', 'sql', 'yaml'])
def test_magika_code_labels_resolve(label: str):
    assert lookup(label).derived
    assert resolve_language(label) is not None


def test_tiktoken_tokenizers():
    assert {tokenizer for tokenizer in Tokenizer if tokenizer.is_tiktoken} == {
        Tokenizer.O200K_BASE,
        Tokenizer.CL100K_BASE,
    }
