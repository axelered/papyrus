import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from papyrus_sdk import AsyncPapyrus, Papyrus


@pytest.fixture(scope='session')
def server() -> tuple[str, str]:
    """Base URL and key of the server under test; unset or unreachable fails, a skip would hide drift"""
    base_url, api_key = os.environ.get('PAPYRUS_BASE_URL'), os.environ.get('PAPYRUS_API_KEY')
    if not base_url or not api_key:
        pytest.fail('set PAPYRUS_BASE_URL and PAPYRUS_API_KEY to a running server, e.g. dekit sdk/server')
    try:
        httpx.get(f'{base_url.rstrip("/")}/health', timeout=5).raise_for_status()
    except httpx.HTTPError as error:
        pytest.fail(f'server under test at {base_url} is unreachable: {error!r}')
    return base_url, api_key


@pytest.fixture(scope='session')
def openapi(server: tuple[str, str]) -> dict[str, Any]:
    response = httpx.get(f'{server[0].rstrip("/")}/openapi.json', timeout=10)
    response.raise_for_status()
    return response.json()


@pytest.fixture
def sdk(server: tuple[str, str]) -> Iterator[Papyrus]:
    with Papyrus(*server) as client:
        yield client


@pytest.fixture
async def async_sdk(server: tuple[str, str]) -> AsyncIterator[AsyncPapyrus]:
    async with AsyncPapyrus(*server) as client:
        yield client


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


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
