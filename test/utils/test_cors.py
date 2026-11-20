from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from server.config import Config, CorsConfig, Settings
from server.factory import create_app

ORIGIN = 'https://docs.axelered.com'
PREFLIGHT = {
    'Origin': ORIGIN,
    'Access-Control-Request-Method': 'POST',
    'Access-Control-Request-Headers': 'authorization',
}


@pytest.fixture
def cors_client(settings: Settings, config: Config, request: pytest.FixtureRequest) -> Iterator[TestClient]:
    origins = getattr(request, 'param', (ORIGIN,))
    app = create_app(settings=settings, config=config.model_copy(update={'cors': CorsConfig(allow_origins=origins)}))
    with TestClient(app) as client:
        yield client


def test_off_by_default_keeps_same_origin(client: TestClient):
    response = client.get('/health', headers={'Origin': ORIGIN})

    assert 'access-control-allow-origin' not in response.headers
    assert client.options('/v1/markdown', headers=PREFLIGHT).status_code == 405


def test_preflight_is_answered_before_auth(cors_client: TestClient):
    response = cors_client.options('/v1/markdown', headers=PREFLIGHT)

    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == ORIGIN
    assert 'access-control-allow-credentials' not in response.headers


def test_response_exposes_request_id(cors_client: TestClient):
    response = cors_client.get('/health', headers={'Origin': ORIGIN})

    assert response.headers['access-control-allow-origin'] == ORIGIN
    assert 'x-request-id' in response.headers['access-control-expose-headers']


def test_errors_carry_cors_headers(cors_client: TestClient):
    response = cors_client.post('/v1/markdown', headers={'Origin': ORIGIN})

    assert response.status_code == 401
    assert response.headers['access-control-allow-origin'] == ORIGIN


def test_other_origins_are_refused(cors_client: TestClient):
    response = cors_client.options('/v1/markdown', headers={**PREFLIGHT, 'Origin': 'https://evil.test'})

    assert response.status_code == 400
    assert 'access-control-allow-origin' not in response.headers


@pytest.mark.parametrize('cors_client', [('*',)], indirect=True)
def test_wildcard_allows_any_origin(cors_client: TestClient):
    response = cors_client.get('/health', headers={'Origin': 'https://any.test'})

    assert response.headers['access-control-allow-origin'] == '*'
