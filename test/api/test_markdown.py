import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fsspec import AbstractFileSystem

from test.fixtures.documents import BINARY, CSV, HTML
from test.fixtures.samples import PYTHON


def _leftovers(work_dir: Path) -> list[Path]:
    return list(work_dir.iterdir()) if work_dir.exists() else []


def test_requires_api_key_before_admission(client: TestClient, app: FastAPI, work_dir: Path):
    response = client.post('/v1/markdown', files={'file': ('a.html', HTML)})

    assert response.status_code == 401
    assert app.state.admission.in_flight == 0
    assert _leftovers(work_dir) == []


def test_upload_returns_json_by_default(client: TestClient, auth_headers: dict[str, str], work_dir: Path):
    response = client.post('/v1/markdown', headers=auth_headers, files={'file': ('report.html', HTML)})

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
            'title': 'Quarterly',
        },
        'options': {'readMode': 'text'},
        'result': {
            'name': 'report.md',
            'size': len('# Report\n\nRevenue grew.'),
            'mimeType': 'text/markdown',
            'extension': 'md',
            'label': 'markdown',
            'group': 'markup',
            'encoding': 'utf-8',
            'content': '# Report\n\nRevenue grew.',
        },
    }
    assert _leftovers(work_dir) == []


def test_mislabeled_source_is_converted_by_content(client: TestClient, auth_headers: dict[str, str]):
    response = client.post('/v1/markdown', headers=auth_headers, files={'file': ('report.pdf', HTML)})

    assert response.status_code == 200
    assert response.json()['source']['label'] == 'html'
    assert response.json()['source']['name'] == 'report.pdf'
    assert response.json()['result']['content'].startswith('# Report')


def test_file_output_returns_markdown_document(client: TestClient, auth_headers: dict[str, str], work_dir: Path):
    response = client.post(
        '/v1/markdown',
        headers=auth_headers,
        data={'metadata': json.dumps({'output': 'file'})},
        files={'file': ('data.csv', CSV)},
    )

    assert response.status_code == 200
    assert response.headers['content-type'] == 'text/markdown; charset=utf-8'
    assert 'filename="data.md"' in response.headers['content-disposition']
    assert '| beta | 2 |' in response.text
    assert _leftovers(work_dir) == []


def test_connector_source_to_destination(
    client: TestClient, auth_headers: dict[str, str], memory_fs: AbstractFileSystem
):
    memory_fs.pipe('docs/report.html', HTML)

    response = client.post(
        '/v1/markdown',
        headers=auth_headers,
        json={'source': 'memory://mem/docs/report.html', 'destination': 'memory://mem/out/report.md'},
    )

    body = response.json()
    assert response.status_code == 200
    assert body['source']['uri'] == 'memory://mem/docs/report.html'
    assert body['source']['mimeType'] == 'text/html'
    assert body['result']['uri'] == 'memory://mem/out/report.md'
    assert body['result']['mimeType'] == 'text/markdown'
    assert 'content' not in body['result'] and 'encoding' not in body['result']
    assert memory_fs.cat('out/report.md').decode().strip() == '# Report\n\nRevenue grew.'
    assert body['result']['size'] == len(memory_fs.cat('out/report.md'))


def test_unsupported_document_is_rejected_before_worker(
    client: TestClient, app: FastAPI, auth_headers: dict[str, str], work_dir: Path
):
    response = client.post('/v1/markdown', headers=auth_headers, files={'file': ('blob.dat', BINARY)})

    assert response.status_code == 415
    assert response.json()['code'] == 'unsupported_format'
    assert 'application/octet-stream' in response.json()['detail']
    assert 'readMode' not in response.json()['detail']
    assert not app.state.services.markdown._pool.started
    assert _leftovers(work_dir) == []


def test_known_binary_format_names_detected_type(client: TestClient, auth_headers: dict[str, str]):
    response = client.post('/v1/markdown', headers=auth_headers, files={'file': ('photo.dat', _png())})

    assert response.status_code == 415
    assert 'image/png (png)' in response.json()['detail']
    assert 'readMode' in response.json()['detail']


def _png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (32, 32), (200, 30, 30)).save(buffer, format='PNG')
    return buffer.getvalue()


def test_openapi_documents_route(client: TestClient):
    operation = client.get('/openapi.json').json()['paths']['/v1/markdown']['post']

    assert set(operation['requestBody']['content']) == {'application/json', 'multipart/form-data'}
    assert set(operation['responses']['200']['content']) == {'application/json', 'text/markdown'}
    assert operation['security']


def test_code_source_is_fenced_with_language(client: TestClient, auth_headers: dict[str, str]):
    response = client.post('/v1/markdown', headers=auth_headers, files={'file': ('loader.py', PYTHON)})

    body = response.json()
    assert response.status_code == 200
    assert body['result']['content'].startswith('```python\nimport os\n')
    assert body['result']['content'].endswith('```\n')
    source = body['source']
    assert 'title' not in source
    assert (source['mimeType'], source['extension'], source['label'], source['group']) == (
        'text/x-python',
        'py',
        'python',
        'code',
    )
