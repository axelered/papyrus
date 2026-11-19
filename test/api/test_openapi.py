from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from server.utils.problem import PROBLEM_JSON

OPS = ['/v1/markdown', '/v1/thumbnail', '/v1/render', '/v1/screenshot']
BASE_STATUSES = {'400', '401', '403', '404', '413', '415', '422', '500', '502', '503', '504'}


@pytest.fixture
def spec(client: TestClient) -> dict[str, Any]:
    return client.get('/openapi.json').json()


def test_spec_is_valid(spec: dict[str, Any]):
    validate(spec)


@pytest.mark.parametrize('path', OPS)
def test_operations_document_the_base_errors(spec: dict[str, Any], path: str):
    responses = spec['paths'][path]['post']['responses']

    assert BASE_STATUSES <= set(responses)
    assert all(PROBLEM_JSON in responses[status]['content'] for status in BASE_STATUSES)
    assert 'WWW-Authenticate' in responses['401']['headers']
    assert 'Retry-After' in responses['503']['headers']


@pytest.mark.parametrize(
    ('path', 'code'), [('/v1/markdown', 'unsupported_format'), ('/v1/render', 'page_out_of_range')]
)
def test_operations_list_their_own_codes(spec: dict[str, Any], path: str, code: str):
    descriptions = ' '.join(response['description'] for response in spec['paths'][path]['post']['responses'].values())

    assert f'`{code}`' in descriptions


@pytest.mark.parametrize('path', OPS)
def test_bodies_are_named_components(spec: dict[str, Any], path: str):
    operation = spec['paths'][path]['post']
    bodies = [operation['requestBody']['content']['application/json']]
    bodies += [response['content']['application/json'] for response in _json_responses(operation)]

    assert all(set(body['schema']) == {'$ref'} for body in bodies)


def test_multipart_metadata_is_the_request_as_json(spec: dict[str, Any]):
    multipart = spec['paths']['/v1/render']['post']['requestBody']['content']['multipart/form-data']

    assert multipart['schema']['properties']['metadata']['$ref'] == '#/components/schemas/RenderRequest'
    assert multipart['encoding'] == {'metadata': {'contentType': 'application/json'}}


def test_problem_code_is_an_enum_of_the_catalog(spec: dict[str, Any]):
    codes = spec['components']['schemas']['Problem']['properties']['code']['enum']

    assert {'unauthorized', 'queue_full', 'not_ready', 'validation_error', 'unsupported_format'} <= set(codes)


def test_wire_names_are_camel_case(spec: dict[str, Any]):
    schemas = spec['components']['schemas']

    assert 'mimeType' in schemas['Source']['properties'] and 'pageCount' in schemas['Result']['properties']
    assert 'fullPage' in schemas['ScreenshotOptions']['properties']
    assert 'maxTokens' in schemas['ChunkingOptions']['properties']


def test_every_field_is_described(spec: dict[str, Any]):
    # the docs site API reference takes field descriptions from the spec only (adr/docs.md)
    missing = [
        f'{name}.{field}'
        for name, schema in spec['components']['schemas'].items()
        for field, value in schema.get('properties', {}).items()
        if not value.get('description')
    ]

    assert missing == []


def test_ready_documents_not_ready(spec: dict[str, Any]):
    response = spec['paths']['/ready']['get']['responses']['503']

    assert response['description'] == 'Service Unavailable: `not_ready`'


def _json_responses(operation: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for response in operation['responses'].values():
        if 'application/json' in response.get('content', {}):
            yield response
