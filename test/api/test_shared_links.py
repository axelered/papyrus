import pytest
from fastapi.testclient import TestClient

from server.io.http import Fetched
from server.io.transfer import Materialized
from test.fixtures.samples import image

SOURCE = 'https://drive.google.com/file/d/1AbC/view'
RESOLVED = 'https://drive.usercontent.google.com/download?id=1AbC&export=download&confirm=t'
LOGIN = b'<html><head><title>Sign in</title></head><body><form>Sign in</form></body></html>'


def _serve(client: TestClient, name: str, content: bytes, link: str | None = 'google-drive') -> None:
    """Stands in for a fetch that went through a share link resolver"""

    async def materialize(uri: str, workspace, parts) -> Materialized:
        path = workspace.new_file(name)
        path.write_bytes(content)
        return Materialized(path, Fetched(path, RESOLVED, 200, link))

    client.app.state.transfer.materialize = materialize


def test_markdown_reports_the_link(client: TestClient, auth_headers: dict[str, str]):
    _serve(client, 'notes.txt', b'Quarterly notes')

    response = client.post('/v1/markdown', json={'source': SOURCE}, headers=auth_headers)

    assert response.status_code == 200, response.text
    source = response.json()['source']
    assert source['fetch'] == {'url': RESOLVED, 'status': 200, 'link': 'google-drive'}
    assert (source['uri'], source['name']) == (SOURCE, 'notes.txt')


def test_thumbnail_reports_the_link(client: TestClient, auth_headers: dict[str, str]):
    _serve(client, 'photo.png', image('PNG', size=(64, 64)))

    response = client.post('/v1/thumbnail', json={'source': SOURCE, 'output': 'json'}, headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()['source']['fetch']['link'] == 'google-drive'


@pytest.mark.parametrize('route', ['markdown', 'thumbnail', 'render', 'inspect'])
def test_link_returning_a_web_page_is_forbidden(client: TestClient, auth_headers: dict[str, str], route: str):
    _serve(client, 'view', LOGIN)

    response = client.post(f'/v1/{route}', json={'source': SOURCE}, headers=auth_headers)

    assert response.status_code == 403, response.text
    assert response.json()['code'] == 'source_forbidden'
    assert 'google-drive link did not return a file' in response.json()['detail']


def test_plain_url_returning_a_web_page_is_processed(client: TestClient, auth_headers: dict[str, str]):
    _serve(client, 'index.html', LOGIN, link=None)

    response = client.post('/v1/markdown', json={'source': 'https://axelered.com/'}, headers=auth_headers)

    assert response.status_code == 200, response.text
    assert 'link' not in response.json()['source']['fetch']
