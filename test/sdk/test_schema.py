"""The SDK mirrors the API contract: a change only on the server fails here (adr/sdk.md)"""

from enum import StrEnum
from typing import Any

import pytest
from fastapi.testclient import TestClient
from papyrus_sdk import models

# facts of the schema the client does not mirror: bounds are the server's, texts are not part of the contract
_IGNORED = {'title', 'description', 'minimum', 'maximum', 'additionalProperties'}
# request envelopes (wire.py builds them) and `GET /`, which the SDK does not call
_NOT_MIRRORED = {'HelloResponse', 'OutputMode'}

MODELS = {
    name: value
    for name, value in vars(models).items()
    if isinstance(value, type) and value.__module__ == models.__name__ and name not in {'ApiModel', 'FileInfo'}
}
BASES = {'OpModel', 'OpResponse'}


@pytest.fixture
def components(client: TestClient) -> dict[str, Any]:
    """Schemas reachable from the paths: the registry is global, other tests add their own models to it"""
    spec = client.get('/openapi.json').json()
    schemas = spec['components']['schemas']
    found: dict[str, Any] = {}
    pending = _refs(spec['paths'])
    while pending:
        name = pending.pop()
        if name not in found:
            found[name] = schemas[name]
            pending |= _refs(schemas[name])
    return found


def _refs(value: Any) -> set[str]:
    if isinstance(value, list):
        return {name for item in value for name in _refs(item)}
    if not isinstance(value, dict):
        return set()
    own = {value['$ref'].rsplit('/', 1)[-1]} if isinstance(value.get('$ref'), str) else set()
    return own | {name for item in value.values() for name in _refs(item)}


def _normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if not isinstance(value, dict):
        return value
    if '$ref' in value:
        value = {**value, '$ref': value['$ref'].rsplit('/', 1)[-1]}
    return {
        key: _normalize(item)
        for key, item in value.items()
        if key not in _IGNORED and not (key == 'default' and item is None)
    }


def _fields(schema: dict[str, Any]) -> dict[str, Any]:
    return {name: _normalize(value) for name, value in schema.get('properties', {}).items()}


def _mirror(name: str) -> dict[str, Any]:
    return MODELS[name].model_json_schema(by_alias=True)


def test_every_component_is_mirrored(components: dict[str, Any]):
    expected = {name for name in components if not name.endswith('Request')} - _NOT_MIRRORED

    assert set(MODELS) - BASES == expected, 'update sdk/papyrus_sdk/models.py'


@pytest.mark.parametrize('name', sorted(n for n, m in MODELS.items() if issubclass(m, StrEnum)))
def test_enums_match(components: dict[str, Any], name: str):
    assert [member.value for member in MODELS[name]] == components[name]['enum']


@pytest.mark.parametrize('name', sorted(n for n, m in MODELS.items() if not issubclass(m, StrEnum) and n not in BASES))
def test_models_match(components: dict[str, Any], name: str):
    mine, theirs = _mirror(name), components[name]
    fields, expected = _fields(mine), _fields(theirs)
    # added by the SDK from a header; the codes of `Problem` stay open so new ones are not rejected
    fields.pop('requestId', None)
    if name == 'Problem':
        expected['code'].pop('enum')

    assert fields == expected, f'update {name} in sdk/papyrus_sdk/models.py'
    assert set(mine.get('required', [])) == set(theirs.get('required', []))
