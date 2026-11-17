import logging
import pickle
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from server.utils.problem import (
    PayloadTooLarge,
    ProblemError,
    ValidationFailed,
    install_problem_handlers,
    problem,
    validation_errors,
)


class Body(BaseModel):
    count: int


class Busy(ProblemError):
    status, code = 503, 'busy'

    def headers(self) -> dict[str, str]:
        return {'Retry-After': '2'}


@pytest.fixture
def failing() -> Iterator[TestClient]:
    app = FastAPI()
    install_problem_handlers(app, logging.getLogger('test.problem'))

    @app.get('/busy')
    async def busy() -> None:
        raise Busy('try later')

    @app.get('/crash')
    async def crash() -> None:
        raise RuntimeError('secret internals')

    @app.post('/validate')
    async def validate(body: Body) -> None:
        return None

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_errors_survive_pickling():
    restored = pickle.loads(pickle.dumps(PayloadTooLarge('big')))

    assert isinstance(restored, PayloadTooLarge)
    assert restored.detail == 'big'
    assert (restored.status, restored.code) == (413, 'payload_too_large')


def test_error_without_detail():
    error = ValidationFailed()

    assert error.detail is None
    assert error.headers() == {}


def test_problem_omits_empty_detail_and_keeps_extras():
    response = problem(422, 'validation_error', errors=[])

    assert response.media_type == 'application/problem+json'
    assert b'"detail"' not in response.body
    assert b'"errors":[]' in response.body


def test_validation_errors_drop_input_and_ctx():
    errors = [{'loc': ('a', 0), 'msg': 'bad', 'type': 'x', 'input': 'secret', 'ctx': {'e': ValueError()}}]

    assert validation_errors(errors) == [{'loc': ['a', 0], 'msg': 'bad', 'type': 'x'}]


def test_problem_error_renders_problem_json(failing: TestClient):
    response = failing.get('/busy')

    assert response.status_code == 503
    assert response.headers['content-type'] == 'application/problem+json'
    assert response.headers['retry-after'] == '2'
    assert response.json() == {
        'type': 'about:blank',
        'title': 'Service Unavailable',
        'status': 503,
        'code': 'busy',
        'detail': 'try later',
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
    assert failing.delete('/busy').json()['code'] == 'method_not_allowed'


def test_unhandled_error_hides_internals(failing: TestClient):
    response = failing.get('/crash')

    assert response.status_code == 500
    assert response.json()['code'] == 'internal_error'
    assert 'secret' not in response.text
