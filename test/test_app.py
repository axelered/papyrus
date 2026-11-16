import logging

import pytest
from fastapi.testclient import TestClient

from server.config import ConfigError, Settings
from server.factory import create_app
from server.logs import JsonFormatter, request_id


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
    import server

    assert not hasattr(server.app.state, 'settings')


def test_echoes_valid_request_id(client: TestClient):
    assert client.get('/health', headers={'X-Request-ID': 'abc-123'}).headers['x-request-id'] == 'abc-123'


@pytest.mark.parametrize('headers', [{}, {'X-Request-ID': 'bad id with spaces'}, {'X-Request-ID': 'x' * 200}])
def test_generates_request_id_when_missing_or_invalid(client: TestClient, headers: dict[str, str]):
    rid = client.get('/health', headers=headers).headers['x-request-id']

    assert len(rid) == 32
    assert rid != headers.get('X-Request-ID')


def test_json_formatter_binds_request_id():
    record = logging.LogRecord('papyrus', logging.INFO, __file__, 1, 'hello %s', ('world',), None)
    token = request_id.set('rid-1')
    try:
        line = JsonFormatter().format(record)
    finally:
        request_id.reset(token)

    assert '"message": "hello world"' in line
    assert '"request_id": "rid-1"' in line
