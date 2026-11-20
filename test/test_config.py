import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from server.config import Config, ConfigError, Settings


def _parse(content: str) -> Config:
    return Config.model_validate(yaml.safe_load(content))


def test_parses_sections():
    content = """
workers: { processes: 3, max_queue: 8 }
limits: { max_file_size: 2MiB, deadline: 10 }
http: { allowed_hosts: [Example.COM] }
cors: { allow_origins: ['https://docs.axelered.com', 'http://localhost:3000'] }
connectors:
  prod: { protocol: s3, options: { endpoint_url: http://minio:9000 } }
"""

    config = _parse(content)

    assert config.workers.processes == 3
    assert config.workers.max_queue == 8
    assert config.limits.max_file_size == 2 * 1024 * 1024
    assert config.limits.deadline == 10
    assert config.http.allowed_hosts == ('example.com',)
    assert config.cors.allow_origins == ('https://docs.axelered.com', 'http://localhost:3000')
    assert config.connectors['prod'].options == {'endpoint_url': 'http://minio:9000'}


def test_auto_processes_resolves_to_cpu_count():
    config = _parse('workers: { processes: auto }')

    assert config.workers.processes == (os.cpu_count() or 1)


@pytest.mark.parametrize(
    'content',
    [
        'unknown: 1',
        'workers: { processes: 0 }',
        'connectors: { Bad.Name: { protocol: s3 } }',
        'connectors: { prod: { options: {} } }',
        "cors: { allow_origins: ['*', 'https://axelered.com'] }",
        'cors: { allow_origins: [https://axelered.com/] }',
        'cors: { allow_origins: [axelered.com] }',
    ],
)
def test_rejects_invalid_config(content: str):
    with pytest.raises(ValidationError):
        _parse(content)


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
