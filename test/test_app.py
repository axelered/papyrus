import logging
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from server.config import Config, ConfigError, Settings
from server.factory import create_app, load_config


def test_startup_fails_without_api_key(tmp_path):
    app = create_app(settings=Settings(config=tmp_path / 'absent.yml', _env_file=None))

    with pytest.raises(ConfigError, match='PAPYRUS_API_KEY'), TestClient(app):
        pass


def test_startup_loads_config_from_settings_path(write_config):
    path = write_config('workers: { max_queue: 3 }')
    app = create_app(settings=Settings(api_key='k', config=path))

    with TestClient(app):
        assert app.state.config.workers.max_queue == 3
        assert app.state.admission.limit == 3


def test_import_has_no_side_effects():
    from server import main

    assert not hasattr(main.app.state, 'settings')


def test_startup_logs_topology_without_secrets(write_config, caplog: pytest.LogCaptureFixture):
    path = write_config('workers: { processes: 4, max_queue: 3 }\nconnectors: { prod: { protocol: memory } }')
    app = create_app(settings=Settings(api_key='secret-key', config=path))
    logger = logging.getLogger('papyrus')
    logger.addHandler(caplog.handler)
    try:
        with TestClient(app):
            pass
    finally:
        logger.removeHandler(caplog.handler)

    assert 'max_queue=3' in caplog.text
    assert 'markdown=4 convert=2 render=4 processes, browser=2 pages' in caplog.text
    assert 'connectors=prod:memory' in caplog.text
    assert 'auth=on (1 keys)' in caplog.text
    assert 'secret-key' not in caplog.text


@pytest.mark.parametrize('module', ['server.services.render', 'server.services.markdown', 'server.services.convert'])
def test_pool_worker_modules_stay_light(module: str):
    # a fresh interpreter, as a pool worker: the app and fastapi would cost every worker its first task
    code = f'import sys, {module}; print(sorted(m for m in ("fastapi", "server.factory") if m in sys.modules))'
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)

    assert result.stdout.strip() == '[]'


def test_echoes_valid_request_id(client: TestClient):
    assert client.get('/health', headers={'X-Request-ID': 'abc-123'}).headers['x-request-id'] == 'abc-123'


@pytest.mark.parametrize('headers', [{}, {'X-Request-ID': 'bad id with spaces'}, {'X-Request-ID': 'x' * 200}])
def test_generates_request_id_when_missing_or_invalid(client: TestClient, headers: dict[str, str]):
    rid = client.get('/health', headers=headers).headers['x-request-id']

    assert len(rid) == 32
    assert rid != headers.get('X-Request-ID')


def test_load_config_missing_file_yields_defaults(tmp_path):
    assert load_config(tmp_path / 'absent.yml') == Config()


def test_load_config_validates_expanded_yaml(write_config):
    config = load_config(write_config('workers: { max_queue: "${QUEUE}" }'), env={'QUEUE': '7'})

    assert config.workers.max_queue == 7


@pytest.mark.parametrize('content', ['- a', 'workers: [', 'workers: { max_queue: "${MISSING}" }'])
def test_load_config_wraps_yaml_failures(write_config, content: str):
    with pytest.raises(ConfigError):
        load_config(write_config(content), env={})
