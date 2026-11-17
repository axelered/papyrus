import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.utils.timing import SERVER_TIMING_HEADER, TimingMiddleware, server_timing, stage


def _app(enabled: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TimingMiddleware, enabled=lambda: enabled)

    @app.get('/work')
    async def work() -> dict[str, bool]:
        with stage('outer'), stage('inner'):
            pass
        return {'ok': True}

    @app.get('/fail')
    async def fail() -> None:
        with stage('broken'):
            raise HTTPException(status_code=422)

    return app


@pytest.fixture
def timing_logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    logger = logging.getLogger('papyrus.timing')
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger='papyrus.timing')
    yield caplog
    logger.removeHandler(caplog.handler)


def _names(header: str) -> list[str]:
    return [entry.split(';')[0] for entry in header.split(', ')]


def test_stage_is_a_noop_outside_a_timed_request():
    with stage('alone'):
        pass


def test_server_timing_format():
    assert server_timing([('detect', 12.345), ('total', 100)]) == 'detect;dur=12.3, total;dur=100.0'


def test_middleware_sends_stages_in_completion_order(timing_logs: pytest.LogCaptureFixture):
    with TestClient(_app(enabled=True)) as client:
        response = client.get('/work')

    assert _names(response.headers[SERVER_TIMING_HEADER]) == ['inner', 'outer', 'total']
    assert 'GET /work inner;dur=' in timing_logs.text


def test_middleware_records_failed_stages():
    with TestClient(_app(enabled=True)) as client:
        response = client.get('/fail')

    assert response.status_code == 422
    assert _names(response.headers[SERVER_TIMING_HEADER]) == ['broken', 'total']


def test_middleware_is_off_when_disabled(timing_logs: pytest.LogCaptureFixture):
    with TestClient(_app(enabled=False)) as client:
        response = client.get('/work')

    assert SERVER_TIMING_HEADER not in response.headers
    assert not timing_logs.records
