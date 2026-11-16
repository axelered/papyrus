from fastapi import FastAPI
from fastapi.testclient import TestClient


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
