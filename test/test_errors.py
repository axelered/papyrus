import pickle

from fastapi.testclient import TestClient

from server.errors import QueueFull, SourceTooLarge, UnsupportedFormat
from server.utils.problem import PayloadTooLarge


def test_app_errors_survive_pickling():
    restored = pickle.loads(pickle.dumps(UnsupportedFormat('nope')))

    assert isinstance(restored, UnsupportedFormat)
    assert restored.detail == 'nope'
    assert (restored.status, restored.code) == (415, 'unsupported_format')


def test_source_too_large_refines_payload_too_large():
    error = SourceTooLarge()

    assert isinstance(error, PayloadTooLarge)
    assert (error.status, error.code) == (413, 'source_too_large')


def test_queue_full_sets_retry_after():
    assert QueueFull().headers() == {'Retry-After': '2'}


def test_app_renders_problem_json(client: TestClient):
    response = client.get('/nope')

    assert response.status_code == 404
    assert response.headers['content-type'] == 'application/problem+json'
    assert response.json()['code'] == 'not_found'
