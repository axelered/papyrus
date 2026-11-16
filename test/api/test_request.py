import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fsspec import AbstractFileSystem
from pydantic import BaseModel

from server.api.output import respond
from server.api.request import OpRequest, op_context, openapi_body
from server.config import Config, LimitsConfig


class EchoOptions(BaseModel):
    delay: float = 0
    fail: bool = False


class EchoRequest(OpRequest):
    output: Literal['file', 'json'] = 'file'
    options: EchoOptions = EchoOptions()


class EchoResult(BaseModel):
    text: str
    parts: list[str]


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def echo(app: FastAPI, calls: list[str]) -> Iterator[TestClient]:
    """Op that copies the source to the output, exercising the whole request lifecycle"""

    @app.post('/echo', openapi_extra=openapi_body(EchoRequest))
    async def echo_route(request: Request):
        async with op_context(request, EchoRequest) as op:
            calls.append(op.model.source)
            source = await request.app.state.transfer.materialize(op.model.source, op.workspace, op.parts)
            await asyncio.sleep(op.model.options.delay)
            if op.model.options.fail:
                raise RuntimeError('boom')
            target = op.workspace.new_file(f'{source.stem}.txt')
            target.write_bytes(source.read_bytes())
            return await respond(
                request,
                op,
                target,
                'text/plain',
                lambda: EchoResult(text=target.read_text(), parts=sorted(op.parts)),
            )

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _metadata(**fields: object) -> dict[str, str]:
    return {'metadata': json.dumps(fields)}


def _leftovers(work_dir: Path) -> list[Path]:
    return list(work_dir.iterdir()) if work_dir.exists() else []


def test_multipart_upload_defaults_source_and_returns_file(echo: TestClient, work_dir: Path):
    response = echo.post('/echo', files={'file': ('notes.md', b'hello')})

    assert response.status_code == 200
    assert response.content == b'hello'
    assert response.headers['content-type'].startswith('text/plain')
    assert 'filename="notes.txt"' in response.headers['content-disposition']
    assert _leftovers(work_dir) == []


def test_multipart_metadata_references_named_part(echo: TestClient):
    response = echo.post(
        '/echo',
        data=_metadata(source='file://doc', output='json'),
        files={'doc': ('a.txt', b'named'), 'file': ('b.txt', b'other')},
    )

    assert response.json() == {'text': 'named', 'parts': ['doc', 'file']}


def test_json_body_with_connector_source(echo: TestClient, memory_fs: AbstractFileSystem, work_dir: Path):
    memory_fs.pipe('bucket/in.html', b'<b>x</b>')

    response = echo.post('/echo', json={'source': 'memory://mem/bucket/in.html', 'output': 'json'})

    assert response.json() == {'text': '<b>x</b>', 'parts': []}
    assert _leftovers(work_dir) == []


def test_destination_writes_result_and_returns_receipt(echo: TestClient, memory_fs: AbstractFileSystem):
    response = echo.post('/echo', data=_metadata(destination='memory://mem/out/r.txt'), files={'file': b'stored'})

    assert response.json() == {'destination': 'memory://mem/out/r.txt', 'content_type': 'text/plain', 'size': 6}
    assert memory_fs.cat('out/r.txt') == b'stored'


@pytest.mark.parametrize(
    ('destination', 'code'),
    [('https://example.com/r.txt', 'unsupported_uri'), ('memory://unknown/r.txt', 'unknown_connector')],
)
def test_invalid_destination_fails_before_processing(echo: TestClient, calls: list[str], destination: str, code: str):
    response = echo.post('/echo', data=_metadata(destination=destination), files={'file': b'x'})

    assert response.status_code == 422
    assert response.json()['code'] == code
    assert calls == []


@pytest.mark.parametrize(
    ('kwargs', 'detail'),
    [
        ({'data': {'metadata': '{bad'}, 'files': {'file': b'x'}}, 'not valid JSON'),
        ({'data': {'metadata': '[1]'}, 'files': {'file': b'x'}}, 'JSON object'),
        ({'data': {'width': '10'}, 'files': {'file': b'x'}}, 'unexpected form field'),
        ({'content': b'[]', 'headers': {'content-type': 'application/json'}}, 'JSON object'),
        ({'data': _metadata(source='file://missing'), 'files': {'file': b'x'}}, 'missing file part'),
    ],
)
def test_rejects_malformed_requests(echo: TestClient, kwargs: dict, detail: str):
    response = echo.post('/echo', **kwargs)

    assert response.status_code == 422
    assert detail in response.json()['detail']


def test_model_validation_errors_are_listed(echo: TestClient):
    response = echo.post('/echo', json={'source': 'memory://mem/a', 'output': 'xml', 'extra': 1})

    body = response.json()
    assert response.status_code == 422
    assert body['code'] == 'validation_error'
    assert {tuple(error['loc']) for error in body['errors']} == {('output',), ('extra',)}


def test_missing_source_without_file_part(echo: TestClient):
    response = echo.post('/echo', data=_metadata(output='json'), files={'other': b'x'})

    assert response.json()['errors'][0]['loc'] == ['source']


def test_json_body_cannot_reference_parts(echo: TestClient):
    response = echo.post('/echo', json={'source': 'file://file'})

    assert response.json()['code'] == 'unsupported_uri'


def test_rejects_other_content_types(echo: TestClient):
    response = echo.post('/echo', content=b'raw', headers={'content-type': 'application/pdf'})

    assert response.status_code == 415
    assert response.json()['code'] == 'unsupported_media_type'


def test_rejects_oversized_upload(echo: TestClient, app: FastAPI, work_dir: Path):
    app.state.config = Config(limits=LimitsConfig(tmp_dir=work_dir, max_file_size=4))

    response = echo.post('/echo', files={'file': b'12345'})

    assert response.status_code == 413
    assert _leftovers(work_dir) == []


def test_queue_full_rejects_without_processing(echo: TestClient, app: FastAPI, calls: list[str]):
    app.state.admission.in_flight = app.state.admission.limit

    response = echo.post('/echo', files={'file': b'x'})

    assert response.status_code == 503
    assert response.headers['retry-after'] == '2'
    assert calls == []


def test_deadline_exceeded(echo: TestClient, app: FastAPI, work_dir: Path):
    app.state.config = Config(limits=LimitsConfig(tmp_dir=work_dir, deadline=0.05))

    response = echo.post('/echo', data=_metadata(options={'delay': 1}), files={'file': b'x'})

    assert response.status_code == 504
    assert response.json()['code'] == 'deadline_exceeded'
    assert _leftovers(work_dir) == []
    assert app.state.admission.in_flight == 0


def test_failure_releases_slot_and_workspace(echo: TestClient, app: FastAPI, work_dir: Path):
    response = echo.post('/echo', data=_metadata(options={'fail': True}), files={'file': b'x'})

    assert response.status_code == 500
    assert _leftovers(work_dir) == []
    assert app.state.admission.in_flight == 0


def test_openapi_documents_both_transports(echo: TestClient):
    body = echo.get('/openapi.json').json()['paths']['/echo']['post']['requestBody']['content']

    assert set(body) == {'application/json', 'multipart/form-data'}
    assert '$ref' not in json.dumps(body)
    assert body['application/json']['schema']['properties']['options']['properties']['delay']
