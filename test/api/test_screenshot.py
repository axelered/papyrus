import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fsspec import AbstractFileSystem

from server.errors import SourceForbidden
from server.services.browser import Capture
from server.types.screenshot import ScreenshotOptions
from server.utils.problem import ProblemError

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 24


class FakeBrowser:
    def __init__(self, error: ProblemError | None = None):
        self.error = error
        self.calls: list[tuple[str, str, ScreenshotOptions]] = []

    async def screenshot(self, url: str, target: Path, options: ScreenshotOptions) -> Capture:
        self.calls.append((url, target.name, options))
        if self.error:
            raise self.error
        target.write_bytes(PNG)
        return Capture(width=options.width, height=options.height, url=f'{url}final', status=200)

    async def aclose(self) -> None:
        pass


def _install(client: TestClient, fake: FakeBrowser) -> FakeBrowser:
    client.app.state.services.browser = fake
    return fake


def _post(client: TestClient, headers: dict[str, str], **body: object):
    return client.post('/v1/screenshot', json=body, headers=headers)


def test_returns_the_image_as_file_by_default(client: TestClient, auth_headers: dict[str, str]):
    fake = _install(client, FakeBrowser())

    response = _post(client, auth_headers, source='https://example.com/page')

    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/png'
    assert 'filename="example.com.png"' in response.headers['content-disposition']
    assert response.content == PNG
    assert fake.calls[0][2] == ScreenshotOptions()


def test_json_output_inlines_the_image(client: TestClient, auth_headers: dict[str, str]):
    _install(client, FakeBrowser())

    response = _post(
        client, auth_headers, source='https://example.com/', output='json', options={'width': 800, 'height': 600}
    )

    assert response.status_code == 200
    assert response.json() == {
        'data': base64.b64encode(PNG).decode(),
        'content_type': 'image/png',
        'width': 800,
        'height': 600,
        'url': 'https://example.com/final',
        'status': 200,
    }


def test_format_sets_media_type_and_extension(client: TestClient, auth_headers: dict[str, str]):
    _install(client, FakeBrowser())

    response = _post(client, auth_headers, source='https://example.com/', options={'format': 'jpeg'})

    assert response.headers['content-type'] == 'image/jpeg'
    assert 'filename="example.com.jpg"' in response.headers['content-disposition']


def test_destination_returns_a_receipt(client: TestClient, auth_headers: dict[str, str], memory_fs: AbstractFileSystem):
    _install(client, FakeBrowser())

    response = _post(client, auth_headers, source='https://example.com/', destination='memory://mem/shots/a.png')

    assert response.json() == {'destination': 'memory://mem/shots/a.png', 'content_type': 'image/png', 'size': len(PNG)}
    assert memory_fs.cat('shots/a.png') == PNG


@pytest.mark.parametrize('options', [{'width': 100}, {'width': 2049, 'scale': 2}, {'format': 'gif'}, {'zoom': 2}])
def test_invalid_options_are_rejected(client: TestClient, auth_headers: dict[str, str], options: dict):
    fake = _install(client, FakeBrowser())

    response = _post(client, auth_headers, source='https://example.com/', options=options)

    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'
    assert fake.calls == []


def test_service_errors_are_problems(client: TestClient, auth_headers: dict[str, str]):
    _install(client, FakeBrowser(SourceForbidden('host is not allowed')))

    response = _post(client, auth_headers, source='https://example.com/')

    assert response.status_code == 403
    assert response.json()['code'] == 'source_forbidden'


@pytest.mark.parametrize(
    ('source', 'status', 'code'),
    [
        ('ftp://example.com/a', 422, 'unsupported_uri'),
        ('memory://mem/a.html', 422, 'unsupported_uri'),
        ('http://127.0.0.1/', 403, 'source_forbidden'),
        ('http://169.254.169.254/latest/meta-data', 403, 'source_forbidden'),
    ],
)
def test_sources_are_checked_before_the_browser_starts(
    client: TestClient, auth_headers: dict[str, str], source: str, status: int, code: str
):
    response = _post(client, auth_headers, source=source)

    assert (response.status_code, response.json()['code']) == (status, code)
    assert client.app.state.services.browser._current is None


def test_uploaded_files_are_not_screenshotted(client: TestClient, auth_headers: dict[str, str]):
    response = client.post('/v1/screenshot', files={'file': ('page.html', b'<p>x</p>')}, headers=auth_headers)

    assert (response.status_code, response.json()['code']) == (422, 'unsupported_uri')


def test_requires_auth(client: TestClient):
    assert client.post('/v1/screenshot', json={'source': 'https://example.com/'}).status_code == 401
