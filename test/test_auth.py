from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from server.auth import is_valid_key, require_api_key
from server.config import Settings
from server.factory import create_app


def _with_protected_route(app: FastAPI) -> FastAPI:
    @app.get('/protected', dependencies=[Depends(require_api_key)])
    async def protected() -> dict[str, bool]:
        return {'ok': True}

    return app


@pytest.fixture
def protected(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(_with_protected_route(app)) as client:
        yield client


def test_is_valid_key_matches_any_configured_key():
    assert is_valid_key('b', ('a', 'b'))
    assert not is_valid_key('c', ('a', 'b'))
    assert not is_valid_key('', ())


def test_accepts_current_and_rotated_keys(protected: TestClient, auth_headers: dict[str, str]):
    assert protected.get('/protected', headers=auth_headers).status_code == 200
    assert protected.get('/protected', headers={'Authorization': 'Bearer old-key'}).status_code == 200


@pytest.mark.parametrize(
    'headers',
    [{}, {'Authorization': 'Bearer wrong'}, {'Authorization': 'Basic dGVzdC1rZXk='}, {'Authorization': 'test-key'}],
)
def test_rejects_missing_or_invalid_credentials(protected: TestClient, headers: dict[str, str]):
    response = protected.get('/protected', headers=headers)

    assert response.status_code == 401
    assert response.headers['content-type'] == 'application/problem+json'
    assert response.headers['www-authenticate'] == 'Bearer'
    assert response.json()['code'] == 'unauthorized'


def test_disabled_auth_skips_check(config):
    app = _with_protected_route(create_app(settings=Settings(auth_disabled=True), config=config))

    with TestClient(app) as client:
        assert client.get('/protected').status_code == 200


def test_health_routes_are_public(client: TestClient):
    assert client.get('/health').status_code == 200
    assert client.get('/ready').status_code == 200
    assert client.get('/').json() == {'status': 'ok', 'name': 'Axelered Papyrus'}
