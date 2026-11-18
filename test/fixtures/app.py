from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.config import Config, LimitsConfig, Settings, WorkersConfig
from server.factory import create_app
from test.fixtures.storage import MEMORY_CONNECTORS

API_KEY = 'test-key'


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(api_key=f'old-key,{API_KEY}', config=tmp_path / 'absent.yml')


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    return tmp_path / 'work'


@pytest.fixture
def config(work_dir: Path) -> Config:
    return Config(
        workers=WorkersConfig(processes=1, warmup=False),
        limits=LimitsConfig(tmp_dir=work_dir),
        connectors=MEMORY_CONNECTORS,
    )


@pytest.fixture
def app(settings: Settings, config: Config) -> FastAPI:
    return create_app(settings=settings, config=config)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {'Authorization': f'Bearer {API_KEY}'}
