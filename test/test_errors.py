import pickle
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from server.errors import PapyrusError, ProcessingTimeout, QueueFull, UnsupportedFormat


class Body(BaseModel):
    count: int


@pytest.fixture
def failing(app: FastAPI) -> Iterator[TestClient]:
    @app.get('/papyrus')
    async def papyrus() -> None:
        raise QueueFull('busy')

    @app.get('/crash')
    async def crash() -> None:
        raise RuntimeError('secret internals')

    @app.post('/validate')
    async def validate(body: Body) -> None:
        return None

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_errors_survive_pickling():
    restored = pickle.loads(pickle.dumps(UnsupportedFormat('nope')))

    assert isinstance(restored, UnsupportedFormat)
    assert restored.detail == 'nope'
    assert (restored.status, restored.code) == (415, 'unsupported_format')


def test_error_without_detail():
    error = ProcessingTimeout()

    assert error.detail is None
    assert isinstance(error, PapyrusError)


def test_papyrus_error_renders_problem_json(failing: TestClient):
    response = failing.get('/papyrus')

    assert response.status_code == 503
    assert response.headers['content-type'] == 'application/problem+json'
    assert response.headers['retry-after'] == '2'
    assert response.json() == {
        'type': 'about:blank',
        'title': 'Service Unavailable',
        'status': 503,
        'code': 'queue_full',
        'detail': 'busy',
    }


def test_validation_error_is_wrapped_without_input_echo(failing: TestClient):
    response = failing.post('/validate', json={'count': 'many'})

    body = response.json()
    assert response.status_code == 422
    assert body['code'] == 'validation_error'
    assert body['errors'] == [{'loc': ['body', 'count'], 'msg': body['errors'][0]['msg'], 'type': 'int_parsing'}]
    assert 'many' not in response.text


def test_unknown_route_is_problem(failing: TestClient):
    response = failing.get('/nope')

    assert response.status_code == 404
    assert response.json()['code'] == 'not_found'


def test_wrong_method_is_problem(failing: TestClient):
    assert failing.delete('/health').json()['code'] == 'method_not_allowed'


def test_unhandled_error_hides_internals(failing: TestClient):
    response = failing.get('/crash')

    assert response.status_code == 500
    assert response.json()['code'] == 'internal_error'
    assert 'secret' not in response.text
