import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.config import Config, ConfigError, Settings, load_config


def test_missing_file_yields_defaults(tmp_path: Path):
    config = load_config(tmp_path / 'absent.yml')

    assert config == Config()
    assert config.workers.processes == (os.cpu_count() or 1)
    assert config.limits.max_file_size == 100_000_000


def test_empty_file_yields_defaults(write_config):
    assert load_config(write_config('')) == Config()


def test_parses_sections(write_config):
    path = write_config(
        """
workers: { processes: 3, max_queue: 8 }
limits: { max_file_size: 2MiB, deadline: 10 }
http: { allowed_hosts: [Example.COM] }
connectors:
  prod: { protocol: s3, options: { endpoint_url: http://minio:9000 } }
"""
    )

    config = load_config(path, env={})

    assert config.workers.processes == 3
    assert config.workers.max_queue == 8
    assert config.limits.max_file_size == 2 * 1024 * 1024
    assert config.limits.deadline == 10
    assert config.http.allowed_hosts == ('example.com',)
    assert config.connectors['prod'].options == {'endpoint_url': 'http://minio:9000'}


def test_auto_processes_resolves_to_cpu_count(write_config):
    config = load_config(write_config('workers: { processes: auto }'), env={})

    assert config.workers.processes == (os.cpu_count() or 1)


def test_expands_env_in_nested_values(write_config):
    path = write_config(
        """
connectors:
  prod:
    protocol: s3
    options:
      key: ${KEY}
      list: [ 'a-${KEY}-b' ]
"""
    )

    config = load_config(path, env={'KEY': 'secret'})

    assert config.connectors['prod'].options == {'key': 'secret', 'list': ['a-secret-b']}


def test_env_values_cannot_inject_yaml(write_config):
    path = write_config('connectors: { prod: { protocol: s3, options: { key: "${KEY}" } } }')

    config = load_config(path, env={'KEY': 'x: y # z'})

    assert config.connectors['prod'].options['key'] == 'x: y # z'


def test_missing_env_var_fails(write_config):
    path = write_config('connectors: { prod: { protocol: s3, options: { key: "${MISSING}" } } }')

    with pytest.raises(ConfigError, match='MISSING'):
        load_config(path, env={})


@pytest.mark.parametrize(
    'content',
    [
        'unknown: 1',
        'workers: { processes: 0 }',
        'connectors: { Bad.Name: { protocol: s3 } }',
        'connectors: { prod: { options: {} } }',
    ],
)
def test_rejects_invalid_config(write_config, content: str):
    with pytest.raises(ValidationError):
        load_config(write_config(content), env={})


@pytest.mark.parametrize('content', ['- a\n- b', 'workers: [', 'just a string'])
def test_rejects_malformed_yaml(write_config, content: str):
    with pytest.raises(ConfigError):
        load_config(write_config(content), env={})


def test_settings_reads_prefixed_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('PAPYRUS_API_KEY', ' k1, ,k2 ')
    monkeypatch.setenv('PAPYRUS_CONFIG', '/tmp/x.yml')

    settings = Settings(_env_file=None)

    assert settings.api_keys == ('k1', 'k2')
    assert settings.config == Path('/tmp/x.yml')


def test_settings_check_fails_closed():
    with pytest.raises(ConfigError, match='PAPYRUS_API_KEY'):
        Settings(api_key=' , ', _env_file=None).check()


def test_settings_check_allows_disabled_auth():
    Settings(auth_disabled=True).check()


def test_settings_reads_dotenv_file(tmp_path: Path):
    env_file = tmp_path / '.env'
    env_file.write_text('PAPYRUS_AUTH_DISABLED=true\nOTHER_TOOL=1\n')

    assert Settings(_env_file=env_file).auth_disabled
