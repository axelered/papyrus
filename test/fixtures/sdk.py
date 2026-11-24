from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from papyrus_sdk import Papyrus

from test.fixtures.app import API_KEY

Handler = Callable[[httpx.Request], httpx.Response]
BASE_URL = 'http://testserver'


@pytest.fixture
def sdk(client: TestClient) -> Papyrus:
    """SDK over the real app, in process"""
    return Papyrus(BASE_URL, API_KEY, http_client=client)


@pytest.fixture
def sdk_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No SDK setting inherited from the shell"""
    monkeypatch.delenv('PAPYRUS_BASE_URL', raising=False)
    monkeypatch.delenv('PAPYRUS_API_KEY', raising=False)
    return monkeypatch


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Records the waits between attempts instead of sleeping"""
    recorded: list[float] = []
    monkeypatch.setattr('papyrus_sdk.client.time.sleep', recorded.append)
    return recorded
