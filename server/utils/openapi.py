from collections import defaultdict
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from server.utils.problem import PROBLEM_JSON, Problem, ProblemError

if TYPE_CHECKING:
    from fastapi import FastAPI

_REF = '#/components/schemas/{model}'


class Components:
    """Models referenced by `$ref` from hand-written OpenAPI fragments; FastAPI only registers response models"""

    def __init__(self) -> None:
        self._models: dict[str, type[BaseModel]] = {}

    def ref(self, model: type[BaseModel]) -> dict[str, str]:
        self._models[model.__name__] = model
        return {'$ref': _REF.format(model=model.__name__)}

    def schemas(self) -> dict[str, Any]:
        if not self._models:
            return {}
        _, top = models_json_schema([(model, 'validation') for model in self._models.values()], ref_template=_REF)
        return top.get('$defs', {})


# process wide: fragments are built when routers are declared, the spec when it is first requested
components = Components()


def problem_responses(*errors: type[ProblemError]) -> dict[int | str, dict[str, Any]]:
    """One problem+json response per status, listing its codes and the headers the errors set"""
    by_status: dict[int, list[type[ProblemError]]] = defaultdict(list)
    for error in errors:
        by_status[error.status].append(error)
    responses: dict[int | str, dict[str, Any]] = {}
    for status, group in sorted(by_status.items()):
        codes = ', '.join(f'`{code}`' for code in sorted({error.code for error in group}))
        response: dict[str, Any] = {
            'description': f'{HTTPStatus(status).phrase}: {codes}',
            'content': {PROBLEM_JSON: {'schema': components.ref(Problem)}},
        }
        headers = {
            name: {'description': f'For example `{value}`', 'schema': {'type': 'string'}}
            for error in group
            for name, value in error().headers().items()
        }
        if headers:
            response['headers'] = headers
        responses[status] = response
    return responses


def install_openapi(app: 'FastAPI') -> None:
    """Adds the registered components to the spec FastAPI generates

    Shared models (op options, enums) are registered by both; the validation schema wins so request defaults stay
    documented, the response echoes the same shape.
    """
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = generate()
            schema.setdefault('components', {}).setdefault('schemas', {}).update(components.schemas())
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
