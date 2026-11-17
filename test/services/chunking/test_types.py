import pytest

from server.services.chunking.types import LANGUAGE_ALIASES, resolve_language
from server.services.detect import lookup
from server.types.markdown import CodeLanguage


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
