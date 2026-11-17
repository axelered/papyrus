import base64
import struct
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from server.config import Config, HttpConfig

pytestmark = pytest.mark.full


class _Page(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'<html><body style="margin:0;background:#0a0"><h1>papyrus</h1></body></html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def page_url() -> Iterator[str]:
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}/'
    server.shutdown()


@pytest.fixture
def config(config: Config) -> Config:
    """The fixture page is on loopback"""
    return config.model_copy(update={'http': HttpConfig(allow_private=True)})


def test_screenshots_a_live_page(client: TestClient, auth_headers: dict[str, str], page_url: str):
    response = client.post(
        '/v1/screenshot',
        json={'source': page_url, 'output': 'json', 'options': {'width': 640, 'height': 480}},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    image = base64.b64decode(body['data'])
    assert struct.unpack('>II', image[16:24]) == (640, 480) == (body['width'], body['height'])
    assert (body['url'], body['status']) == (page_url, 200)


def test_file_output_streams_the_image(client: TestClient, auth_headers: dict[str, str], page_url: str):
    response = client.post(
        '/v1/screenshot', json={'source': page_url, 'options': {'format': 'webp'}}, headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'image/webp'
    assert response.content.startswith(b'RIFF') and response.content[8:12] == b'WEBP'
