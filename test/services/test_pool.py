import asyncio
import time
from collections.abc import Iterator

import pytest

from server.errors import ProcessingFailed, ProcessingTimeout, UnsupportedFormat
from server.services.pool import ProcessPool
from test.services import workers

pytestmark = pytest.mark.anyio


@pytest.fixture(scope='module')
def pool() -> Iterator[ProcessPool]:
    pool = ProcessPool('test', processes=1, timeout=3)
    yield pool
    pool.close()


async def test_starts_lazily_and_runs_tasks():
    lazy = ProcessPool('lazy', processes=1, timeout=5)
    try:
        assert not lazy.started
        assert await lazy.run(workers.add, 2, 3) == 5
        assert lazy.started
    finally:
        lazy.close()
    assert not lazy.started


async def test_preserves_papyrus_errors(pool: ProcessPool):
    with pytest.raises(UnsupportedFormat, match='nope'):
        await pool.run(workers.raise_papyrus)


async def test_wraps_generic_errors(pool: ProcessPool):
    with pytest.raises(ProcessingFailed, match='KeyError'):
        await pool.run(workers.raise_generic)


async def test_worker_crash_is_processing_failure(pool: ProcessPool):
    with pytest.raises(ProcessingFailed, match='died'):
        await pool.run(workers.crash)

    assert await pool.run(workers.add, 1, 1) == 2


async def test_timeout_kills_worker_and_pool_recovers():
    short = ProcessPool('short', processes=1, timeout=0.5)
    try:
        before = await short.run(workers.pid)
        with pytest.raises(ProcessingTimeout):
            await short.run(workers.sleep, 30)
        assert await short.run(workers.pid) != before
    finally:
        short.close()


async def test_cancellation_terminates_running_task(pool: ProcessPool):
    before = await pool.run(workers.pid)
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5):
            await pool.run(workers.sleep, 30)

    assert await pool.run(workers.pid) != before
    assert time.monotonic() - started < 10


def test_close_is_idempotent():
    pool = ProcessPool('idle', processes=1, timeout=1)

    pool.close()
    pool.close()
