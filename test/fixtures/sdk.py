import httpx
import pytest
from fastapi.testclient import TestClient
from papyrus_sdk import Papyrus

from test.fixtures.app import API_KEY

BASE_URL = 'http://testserver'


class Server:
    """Mock transport handler: replays the outcomes in order, the last one forever, and records the requests"""

    def __init__(self, *outcomes: httpx.Response | Exception):
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.outcomes[min(len(self.requests), len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def problem(status: int, code: str, **headers: str) -> httpx.Response:
    body = {'title': 'Error', 'status': status, 'code': code, 'detail': 'why'}
    return httpx.Response(status, json=body, headers={**headers, 'content-type': 'application/problem+json'})


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
    monkeypatch.setattr('papyrus_sdk.client.sleep', recorded.append)

    async def async_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr('papyrus_sdk.client.async_sleep', async_sleep)
    return recorded
