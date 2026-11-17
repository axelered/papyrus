import base64
import io

import pytest
from fastapi.testclient import TestClient
from fsspec import AbstractFileSystem
from PIL import Image

from test.fixtures.samples import image, pdf_pages, png_header

A4 = (595, 842)


def _upload(client: TestClient, route: str, headers: dict[str, str], name: str, content: bytes, metadata: str = '{}'):
    return client.post(f'/v1/{route}', files={'file': (name, content)}, data={'metadata': metadata}, headers=headers)


def _size(content: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(content)).size


def test_thumbnail_defaults_to_a_webp_file(client: TestClient, auth_headers: dict[str, str]):
    response = _upload(client, 'thumbnail', auth_headers, 'report.pdf', pdf_pages(A4))

    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'image/webp'
    assert 'filename="report.webp"' in response.headers['content-disposition']
    assert _size(response.content) == (181, 256)


def test_thumbnail_cover_json(client: TestClient, auth_headers: dict[str, str]):
    metadata = '{"output": "json", "options": {"width": 128, "height": 128, "fit": "cover", "format": "jpeg"}}'

    response = _upload(client, 'thumbnail', auth_headers, 'photo.jpg', image('JPEG', size=(400, 200)), metadata)

    body = response.json()
    assert (body['width'], body['height'], body['content_type']) == (128, 128, 'image/jpeg')
    assert (body['page'], body['page_count']) == (1, 1)
    assert body['source_type']['group'] == 'image'
    assert _size(base64.b64decode(body['data'])) == (128, 128)


def test_render_page_at_default_dpi(client: TestClient, auth_headers: dict[str, str]):
    metadata = '{"output": "json", "options": {"page": 2}}'

    response = _upload(client, 'render', auth_headers, 'report.pdf', pdf_pages(A4, A4), metadata)

    body = response.json()
    assert (body['width'], body['height'], body['page'], body['page_count']) == (1240, 1755, 2, 2)
    assert body['source_type']['label'] == 'pdf'


def test_render_file_is_named_by_page(client: TestClient, auth_headers: dict[str, str]):
    response = _upload(client, 'render', auth_headers, 'report.pdf', pdf_pages(A4, A4), '{"options": {"page": 2}}')

    assert response.headers['content-type'] == 'image/png'
    assert 'filename="report-2.png"' in response.headers['content-disposition']


def test_render_image_ignores_dpi(client: TestClient, auth_headers: dict[str, str]):
    metadata = '{"options": {"dpi": 300}}'

    response = _upload(client, 'render', auth_headers, 'shot.png', image('PNG', size=(320, 200)), metadata)

    assert _size(response.content) == (320, 200)


def test_destination_receipt_reports_source_type(
    client: TestClient, auth_headers: dict[str, str], memory_fs: AbstractFileSystem
):
    memory_fs.pipe('docs/report.pdf', pdf_pages(A4))

    response = client.post(
        '/v1/thumbnail',
        json={'source': 'memory://mem/docs/report.pdf', 'destination': 'memory://mem/thumbs/report.webp'},
        headers=auth_headers,
    )

    body = response.json()
    assert (body['destination'], body['content_type']) == ('memory://mem/thumbs/report.webp', 'image/webp')
    assert body['source_type']['label'] == 'pdf'
    assert _size(memory_fs.cat('thumbs/report.webp')) == (181, 256)


@pytest.mark.parametrize(
    ('route', 'name', 'content', 'metadata', 'status', 'code'),
    [
        ('thumbnail', 'notes.txt', b'plain text ' * 20, '{}', 415, 'unsupported_format'),
        ('render', 'report.pdf', pdf_pages(A4), '{"options": {"page": 3}}', 422, 'page_out_of_range'),
        ('render', 'report.pdf', pdf_pages(A4), '{"options": {"dpi": 72, "width": 100}}', 422, 'validation_error'),
        ('thumbnail', 'report.pdf', pdf_pages(A4), '{"options": {"width": 5000}}', 422, 'validation_error'),
        ('thumbnail', 'bomb.png', png_header(30_000, 30_000), '{}', 413, 'image_too_large'),
        ('render', 'broken.pdf', b'%PDF-1.7 broken', '{}', 415, 'unsupported_format'),
    ],
)
def test_errors(
    client: TestClient,
    auth_headers: dict[str, str],
    route: str,
    name: str,
    content: bytes,
    metadata: str,
    status: int,
    code: str,
):
    response = _upload(client, route, auth_headers, name, content, metadata)

    assert (response.status_code, response.json()['code']) == (status, code)


@pytest.mark.parametrize('route', ['thumbnail', 'render'])
def test_openapi_documents_route(client: TestClient, route: str):
    operation = client.get('/openapi.json').json()['paths'][f'/v1/{route}']['post']

    assert set(operation['requestBody']['content']) == {'application/json', 'multipart/form-data'}
    assert set(operation['responses']['200']['content']) == {
        'application/json',
        'image/png',
        'image/jpeg',
        'image/webp',
    }
    assert operation['security']
