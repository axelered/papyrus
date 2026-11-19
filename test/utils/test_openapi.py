from typing import ClassVar

from fastapi import FastAPI
from pydantic import BaseModel

from server.utils.openapi import Components, install_openapi, problem_responses
from server.utils.problem import PROBLEM_JSON, ProblemError, problem_codes


class Teapot(ProblemError):
    status, code = 418, 'teapot'


class Busy(ProblemError):
    status, code = 418, 'busy'
    wait: ClassVar[str] = '5'

    def headers(self) -> dict[str, str]:
        return {'Retry-After': self.wait}


class Item(BaseModel):
    name: str


def test_groups_errors_by_status_with_codes_and_headers():
    responses = problem_responses(Teapot, Busy, ProblemError)

    assert set(responses) == {418, 500}
    assert responses[418]['description'] == "I'm a Teapot: `busy`, `teapot`"
    assert responses[418]['headers']['Retry-After']['schema'] == {'type': 'string'}
    assert responses[418]['content'][PROBLEM_JSON]['schema'] == {'$ref': '#/components/schemas/Problem'}
    assert 'headers' not in responses[500]


def test_problem_codes_include_subclasses_and_handler_codes():
    codes = problem_codes()

    assert {'teapot', 'busy', 'internal_error', 'not_found', 'http_error'} <= set(codes)
    assert codes == sorted(set(codes))


def test_registered_models_become_components():
    registry = Components()

    assert registry.ref(Item) == {'$ref': '#/components/schemas/Item'}
    assert registry.schemas()['Item']['properties']['name']['type'] == 'string'


def test_install_adds_components_once():
    app = FastAPI()
    problem_responses(Teapot)
    install_openapi(app)

    spec = app.openapi()

    assert 'Problem' in spec['components']['schemas']
    assert app.openapi() is spec
