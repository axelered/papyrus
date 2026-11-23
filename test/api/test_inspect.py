import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fsspec import AbstractFileSystem

from server.io.http import Fetched
from server.io.transfer import Materialized
from test.fixtures.documents import BINARY, CSV, HTML
from test.fixtures.samples import PYTHON, SAMPLES


def _leftovers(work_dir: Path) -> list[Path]:
    return list(work_dir.iterdir()) if work_dir.exists() else []


def _upload(client: TestClient, headers: dict[str, str], name: str, content: bytes, **metadata: object):
    data = {'metadata': json.dumps(metadata)} if metadata else None
    return client.post('/v1/inspect', headers=headers, data=data, files={'file': (name, content)})


def test_requires_api_key_before_admission(client: TestClient, app: FastAPI, work_dir: Path):
    response = client.post('/v1/inspect', files={'file': ('a.html', HTML)})

    assert response.status_code == 401
    assert app.state.admission.in_flight == 0
    assert _leftovers(work_dir) == []


def test_upload_returns_source_and_checks(client: TestClient, auth_headers: dict[str, str], work_dir: Path):
    response = _upload(client, auth_headers, 'report.html', HTML)

    assert response.status_code == 200
    assert response.json() == {
        'source': {
            'uri': 'file://file',
            'name': 'report.html',
            'size': len(HTML),
            'mimeType': 'text/html',
            'extension': 'html',
            'label': 'html',
            'group': 'markup',
        },
        'options': {},
        'checks': {'extensionMatch': True},
    }
    assert _leftovers(work_dir) == []


@pytest.mark.parametrize(
    ('name', 'content', 'label', 'match'),
    [
        ('data.csv', CSV, 'csv', True),
        ('page.htm', HTML, 'html', True),
        ('notes.txt', PYTHON, 'python', True),
        ('book.docx', SAMPLES['docx'](), 'docx', True),
        ('photo.jpeg', SAMPLES['jpeg'](), 'jpeg', True),
        ('blob.bin', BINARY, 'unknown', True),
        ('report.pdf', HTML, 'html', False),
        ('photo.html', SAMPLES['png'](), 'png', False),
        ('data.xyz', HTML, 'html', False),
        ('blob.bin', HTML, 'html', False),
    ],
)
def test_extension_match(
    client: TestClient, auth_headers: dict[str, str], name: str, content: bytes, label: str, match: bool
):
    body = _upload(client, auth_headers, name, content).json()

    assert (body['source']['name'], body['source']['label']) == (name, label)
    assert body['checks'] == {'extensionMatch': match}


def test_name_without_extension_omits_the_check(client: TestClient, auth_headers: dict[str, str]):
    body = _upload(client, auth_headers, 'README', HTML).json()

    assert body['source']['label'] == 'html'
    assert body['checks'] == {}


def test_unknown_content_is_inspected(client: TestClient, auth_headers: dict[str, str]):
    response = _upload(client, auth_headers, 'blob', BINARY)

    assert response.status_code == 200
    assert response.json()['source']['label'] == 'unknown'
    assert response.json()['source']['mimeType'] == 'application/octet-stream'


def test_connector_source(client: TestClient, auth_headers: dict[str, str], memory_fs: AbstractFileSystem):
    memory_fs.pipe('docs/report.pdf', HTML)

    response = client.post('/v1/inspect', headers=auth_headers, json={'source': 'memory://mem/docs/report.pdf'})

    assert response.status_code == 200
    body = response.json()
    assert body['source']['uri'] == 'memory://mem/docs/report.pdf'
    assert (body['source']['name'], body['source']['label']) == ('report.pdf', 'html')
    assert body['checks'] == {'extensionMatch': False}


def test_http_source_reports_fetch(client: TestClient, auth_headers: dict[str, str]):
    async def materialize(uri: str, workspace, parts) -> Materialized:
        # a URL without an extension is named after the response Content-Type
        path = workspace.new_file('source.html')
        path.write_bytes(HTML)
        return Materialized(path, Fetched(path, 'https://axelered.com/home', 200))

    client.app.state.transfer.materialize = materialize

    response = client.post('/v1/inspect', headers=auth_headers, json={'source': 'https://axelered.com/'})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['source']['fetch'] == {'url': 'https://axelered.com/home', 'status': 200}
    assert body['checks'] == {'extensionMatch': True}


@pytest.mark.parametrize('field', [{'output': 'file'}, {'destination': 'memory://mem/out/a.json'}])
def test_output_fields_are_rejected(client: TestClient, auth_headers: dict[str, str], field: dict[str, str]):
    response = _upload(client, auth_headers, 'report.html', HTML, **field)

    assert (response.status_code, response.json()['code']) == (422, 'validation_error')
