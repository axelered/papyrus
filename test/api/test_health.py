import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import version


def test_root_reports_placeholder_version_without_watermark(client: TestClient):
    assert client.get('/').json() == {
        'status': 'ok',
        'name': 'Axelered Papyrus',
        'version': version.__version__,
        'watermark': None,
    }


def test_root_reports_stamped_version_and_watermark(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(version, '__version__', '1.2.3')
    monkeypatch.setattr(version, '__watermark__', 'v1.2.3 #2e6dcdd 2026-11-17')

    body = client.get('/').json()

    assert (body['version'], body['watermark']) == ('1.2.3', 'v1.2.3 #2e6dcdd 2026-11-17')


def test_health_is_always_ok(client: TestClient, app: FastAPI):
    app.state.admission.in_flight = app.state.admission.limit

    assert client.get('/health').json() == {'status': 'ok'}


def test_ready_reports_ok_with_capacity(client: TestClient):
    response = client.get('/ready')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_ready_fails_when_admission_exhausted(client: TestClient, app: FastAPI):
    app.state.admission.in_flight = app.state.admission.limit

    response = client.get('/ready')

    assert response.status_code == 503
    assert response.json()['code'] == 'not_ready'
