"""The SDK mirrors the API contract: a change only on the server fails here (adr/sdk-python.md)"""

import inspect
from enum import StrEnum
from typing import Any, Literal, get_args, get_origin, get_type_hints

import pytest
from fastapi.testclient import TestClient
from papyrus_sdk import AsyncPapyrus, Papyrus, models
from pydantic.alias_generators import to_camel

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


OPS = ['markdown', 'render', 'thumbnail', 'screenshot', 'inspect']
# how a document is passed and the call itself, not options of the operation
_INPUT = {'document', 'source', 'file', 'filename', 'url', 'timeout'}


def _parameters(operation: str) -> set[str]:
    return set(inspect.signature(getattr(Papyrus, operation)).parameters) - {'self'} - _INPUT


def test_every_operation_has_a_method(client: TestClient):
    paths = client.get('/openapi.json').json()['paths']

    assert {path for path in paths if path.startswith('/v1/')} == {f'/v1/{operation}' for operation in OPS}


@pytest.mark.parametrize('operation', OPS)
def test_parameters_match_the_request(components: dict[str, Any], operation: str):
    request = components[f'{operation.capitalize()}Request']['properties']
    options = components[f'{operation.capitalize()}Options']['properties']
    expected = set(options) | ({'destination'} & set(request))

    assert {to_camel(name) for name in _parameters(operation)} == expected, 'update sdk/papyrus_sdk/client.py'
    # `output` is fixed to json, everything else of the envelope has a parameter
    assert set(request) <= {'source', 'options', 'output', 'destination'}


@pytest.mark.parametrize('operation', OPS)
def test_literals_match_their_enum(operation: str):
    for annotation in get_type_hints(getattr(Papyrus, operation)).values():
        enums = [arg for arg in get_args(annotation) if isinstance(arg, type) and issubclass(arg, StrEnum)]
        literals = [arg for arg in get_args(annotation) if get_origin(arg) is Literal]
        assert [get_args(literal) for literal in literals] == [tuple(enum) for enum in enums]


def _public(client: type) -> dict[str, inspect.Signature]:
    methods = inspect.getmembers(client, inspect.isfunction)
    return {name: inspect.signature(method) for name, method in methods if not name.startswith('_')}


def test_both_clients_have_the_same_methods_and_parameters():
    mine, theirs = _public(Papyrus), _public(AsyncPapyrus)
    close, aclose = mine.pop('close'), theirs.pop('aclose')

    assert close.parameters == aclose.parameters
    assert set(mine) == set(theirs)
    for name, signature in mine.items():
        # uploads are typed narrower in the async client
        for ours, other in zip(signature.parameters.values(), theirs[name].parameters.values(), strict=True):
            narrowed = {'document', 'file'}
            assert (ours.name, ours.kind, ours.default) == (other.name, other.kind, other.default)
            assert ours.name in narrowed or ours.annotation == other.annotation
        assert signature.return_annotation == theirs[name].return_annotation
        assert inspect.iscoroutinefunction(getattr(AsyncPapyrus, name))
