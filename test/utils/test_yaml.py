from pathlib import Path

import pytest

from server.utils.yaml import expand_env, load_yaml


def test_empty_file_is_empty_mapping(write_config):
    assert load_yaml(write_config(''), {}) == {}


def test_expands_env_in_nested_values(write_config):
    path = write_config("a:\n  key: ${KEY}\n  list: [ 'a-${KEY}-b', 3 ]\n")

    assert load_yaml(path, {'KEY': 'secret'}) == {'a': {'key': 'secret', 'list': ['a-secret-b', 3]}}


def test_env_values_cannot_inject_yaml(write_config):
    assert load_yaml(write_config('key: "${KEY}"'), {'KEY': 'x: y # z'}) == {'key': 'x: y # z'}


def test_missing_env_var_fails():
    with pytest.raises(ValueError, match='MISSING'):
        expand_env({'key': '${MISSING}'}, {})


@pytest.mark.parametrize('content', ['- a\n- b', 'key: [', 'just a string'])
def test_rejects_malformed_yaml(write_config, content: str):
    with pytest.raises(ValueError):
        load_yaml(write_config(content), {})


def test_missing_file_is_not_hidden(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_yaml(tmp_path / 'absent.yml', {})
