import json

import pytest
from fastapi.testclient import TestClient
from fsspec import AbstractFileSystem

from test.fixtures.samples import HTML, PYTHON

pytestmark = pytest.mark.usefixtures('chunking_assets')

REPORT = b"""<html><head><title>Quarterly</title></head><body>
<h1>Report</h1><h2>Revenue</h2><p>Revenue grew in every region this quarter.</p>
<table><tr><th>Region</th><th>Q1</th></tr><tr><td>EU</td><td>10</td></tr><tr><td>US</td><td>20</td></tr></table>
<h2>Outlook</h2><p>Next quarter looks stable.</p></body></html>"""


def _post(client: TestClient, headers: dict[str, str], name: str, content: bytes, **fields: object):
    return client.post(
        '/v1/markdown', headers=headers, data={'metadata': json.dumps(fields)}, files={'file': (name, content)}
    )


def test_returns_structured_chunks(client: TestClient, auth_headers: dict[str, str]):
    response = _post(client, auth_headers, 'report.html', REPORT, options={'chunking': {'maxTokens': 64}})

    body = response.json()
    assert response.status_code == 200
    assert {chunk['kind'] for chunk in body['chunks']} == {'text', 'table'}
    assert [chunk['index'] for chunk in body['chunks']] == list(range(len(body['chunks'])))
    revenue = next(chunk for chunk in body['chunks'] if 'Revenue grew' in chunk['text'])
    assert revenue['headings'] == ['Report', 'Revenue']
    assert body['result']['content'][revenue['start'] : revenue['end']] == revenue['text']
    assert set(revenue) == {'index', 'kind', 'text', 'headings', 'start', 'end', 'tokens'}
    assert body['options']['chunking'] == {'maxTokens': 64, 'overlap': 0, 'tokenizer': 'o200k_base'}


def test_without_chunking_has_no_chunks_key(client: TestClient, auth_headers: dict[str, str]):
    assert 'chunks' not in _post(client, auth_headers, 'report.html', HTML).json()


def test_code_source_chunks_carry_language(client: TestClient, auth_headers: dict[str, str]):
    response = _post(client, auth_headers, 'loader.py', PYTHON, options={'chunking': {'maxTokens': 64}})

    chunks = response.json()['chunks']
    assert chunks
    assert all(chunk['kind'] == 'code' and chunk['language'] == 'python' for chunk in chunks)
    assert all(chunk['text'].startswith('```python\n') for chunk in chunks)


def test_destination_receives_the_json_document(
    client: TestClient, auth_headers: dict[str, str], memory_fs: AbstractFileSystem
):
    response = _post(
        client,
        auth_headers,
        'report.html',
        REPORT,
        destination='memory://mem/out/report.json',
        options={'chunking': {'tokenizer': 'word', 'maxTokens': 64}},
    )

    body = response.json()
    assert set(body) == {'source', 'options', 'result'}
    assert body['result']['uri'] == 'memory://mem/out/report.json'
    assert (body['result']['name'], body['result']['mimeType']) == ('report.json', 'application/json')
    assert 'content' not in body['result']
    stored = json.loads(memory_fs.cat('out/report.json'))
    assert body['result']['size'] == len(memory_fs.cat('out/report.json'))
    assert stored['chunks'] and stored['source']['label'] == 'html'
    assert stored['result']['name'] == 'report.md' and stored['result']['content'].startswith('# Report')


def test_file_output_returns_the_json_document(client: TestClient, auth_headers: dict[str, str]):
    response = _post(client, auth_headers, 'report.html', REPORT, output='file', options={'chunking': {}})

    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/json'
    assert 'filename="report.json"' in response.headers['content-disposition']
    assert response.json()['chunks'] and response.json()['result']['encoding'] == 'utf-8'


@pytest.mark.parametrize(
    ('fields', 'loc'),
    [
        ({'options': {'chunking': {'maxTokens': 10}}}, ['options', 'chunking', 'maxTokens']),
        ({'options': {'chunking': {'maxTokens': 64, 'overlap': 64}}}, ['options', 'chunking']),
        ({'options': {'chunking': {'max_tokens': 64}}}, ['options', 'chunking', 'max_tokens']),
        ({'options': {'chunking': {'tokenizer': 'gpt2'}}}, ['options', 'chunking', 'tokenizer']),
        ({'options': {'chunking': {'size': 1}}}, ['options', 'chunking', 'size']),
        ({'options': {'other': True}}, ['options', 'other']),
    ],
)
def test_rejects_invalid_chunking_options(client: TestClient, auth_headers: dict[str, str], fields: dict, loc: list):
    response = _post(client, auth_headers, 'report.html', HTML, **fields)

    assert response.status_code == 422
    assert response.json()['errors'][0]['loc'] == loc


def test_openapi_exposes_enums(client: TestClient):
    schemas = client.get('/openapi.json').json()['components']['schemas']

    tokenizer = json.dumps(schemas['Tokenizer'])
    assert schemas['ChunkingOptions']['properties']['tokenizer']['$ref'] == '#/components/schemas/Tokenizer'
    assert 'o200k_base' in tokenizer and 'cl100k_base' in tokenizer
