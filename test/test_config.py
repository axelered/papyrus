import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from server.config import Config, ConfigError, MistralConfig, QwenConfig, Settings


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


@pytest.mark.parametrize('locale', ['en-US', 'de', 'pt-BR', 'zh-Hant-TW', 'es-419', None])
def test_accepts_bcp47_locales(locale: str | None):
    assert Config.model_validate({'http': {'locale': locale}}).http.locale == locale


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
        'http: { locale: it_IT }',
        'http: { locale: italian }',
        "http: { locale: '' }",
        'ocr: {}',
        'ocr: { type: dots, model: m, url: "http://vllm/v1" }',
        'ocr: { type: qwen, model: m }',
        'ocr: { type: qwen, url: "http://vllm/v1" }',
        'ocr: { type: qwen, model: m, url: vllm }',
        'ocr: { type: qwen, model: m, url: "http://vllm/v1", api_key: "" }',
        'ocr: { type: qwen, model: m, url: "http://vllm/v1", concurrency: 0 }',
        'ocr: { type: mistral }',
        'ocr: { type: mistral, api_key: k, extra_body: {} }',
    ],
)
def test_rejects_invalid_config(content: str):
    with pytest.raises(ValidationError):
        _parse(content)


def test_ocr_is_off_by_default():
    assert Config().ocr is None


def test_parses_ocr_backends():
    qwen = _parse('ocr: { type: qwen, model: qwen-vl, url: "http://vllm:8000/v1", extra_body: { top_k: 1 } }').ocr
    mistral = _parse('ocr: { type: mistral, api_key: secret, max_pages: 5 }').ocr

    assert isinstance(qwen, QwenConfig) and isinstance(mistral, MistralConfig)
    assert (qwen.api_key, qwen.extra_body, qwen.concurrency, qwen.max_pages) == (None, {'top_k': 1}, 8, 100)
    assert (mistral.model, mistral.url, mistral.max_pages) == ('mistral-ocr-latest', None, 5)
    assert 'secret' not in repr(mistral)


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
