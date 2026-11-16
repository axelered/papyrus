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
        'markdown': '# Report\n\nRevenue grew.',
        'title': 'Quarterly',
        'source_type': {'mime_type': 'text/html', 'extension': 'html', 'label': 'html', 'group': 'markup'},
    }
    assert _leftovers(work_dir) == []


def test_mislabeled_source_is_converted_by_content(client: TestClient, auth_headers: dict[str, str]):
    response = client.post('/v1/markdown', headers=auth_headers, files={'file': ('report.pdf', HTML)})

    assert response.status_code == 200
    assert response.json()['source_type']['label'] == 'html'
    assert response.json()['markdown'].startswith('# Report')


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

    assert response.status_code == 200
    assert response.json()['destination'] == 'memory://mem/out/report.md'
    assert response.json()['source_type']['mime_type'] == 'text/html'
    assert memory_fs.cat('out/report.md').decode().strip() == '# Report\n\nRevenue grew.'


def test_unsupported_document_is_rejected_before_worker(
    client: TestClient, app: FastAPI, auth_headers: dict[str, str], work_dir: Path
):
    response = client.post('/v1/markdown', headers=auth_headers, files={'file': ('blob.dat', BINARY)})

    assert response.status_code == 415
    assert response.json()['code'] == 'unsupported_format'
    assert 'application/octet-stream' in response.json()['detail']
    assert not app.state.services.markdown._pool.started
    assert _leftovers(work_dir) == []


def test_known_binary_format_names_detected_type(client: TestClient, auth_headers: dict[str, str]):
    response = client.post('/v1/markdown', headers=auth_headers, files={'file': ('photo.dat', _png())})

    assert response.status_code == 415
    assert 'image/png (png)' in response.json()['detail']


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
    assert body['markdown'].startswith('```python\nimport os\n')
    assert body['markdown'].endswith('```\n')
    assert body['title'] is None
    assert body['source_type'] == {'mime_type': 'text/x-python', 'extension': 'py', 'label': 'python', 'group': 'code'}
