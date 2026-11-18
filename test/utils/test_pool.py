import asyncio
import time
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path

import pytest

from server.errors import ProcessingFailed, ProcessingTimeout, UnsupportedFormat
from server.services.base import pool_errors
from server.utils.pool import PoolFailed, PoolTimeout, ProcessPool
from test.utils import workers

pytestmark = pytest.mark.anyio


@pytest.fixture(scope='module')
async def pool() -> AsyncIterator[ProcessPool]:
    async with ProcessPool('test', processes=1, timeout=3) as pool:
        yield pool


async def test_starts_lazily_and_runs_tasks():
    async with ProcessPool('lazy', processes=1, timeout=5) as lazy:
        assert not lazy.started
        assert await lazy.run(workers.add, 2, 3) == 5
        assert lazy.started
    assert not lazy.started


async def test_start_waits_for_every_initialized_worker(tmp_path: Path):
    async with ProcessPool('warm', processes=2, timeout=5, initializer=partial(workers.mark, str(tmp_path))) as warm:
        await warm.start()

        assert warm.started
        assert len(list(tmp_path.iterdir())) == 2


async def test_start_fails_on_initializer_error_and_tasks_still_run():
    async with ProcessPool('broken', processes=1, timeout=5, initializer=workers.fail_init) as broken:
        with pytest.raises(PoolFailed, match='initialization failed'):
            await broken.start()

        assert await broken.run(workers.add, 1, 2) == 3


async def test_preserves_papyrus_errors(pool: ProcessPool):
    with pytest.raises(UnsupportedFormat, match='nope'):
        await pool.run(workers.raise_papyrus)


async def test_wraps_generic_errors(pool: ProcessPool):
    with pytest.raises(PoolFailed, match='KeyError'):
        await pool.run(workers.raise_generic)


async def test_worker_crash_is_pool_failure(pool: ProcessPool):
    with pytest.raises(PoolFailed, match='died'):
        await pool.run(workers.crash)

    assert await pool.run(workers.add, 1, 1) == 2


async def test_timeout_kills_worker_and_pool_recovers():
    async with ProcessPool('short', processes=1, timeout=0.5) as short:
        before = await short.run(workers.pid)
        with pytest.raises(PoolTimeout):
            await short.run(workers.sleep, 30)
        assert await short.run(workers.pid) != before


async def test_cancellation_terminates_running_task(pool: ProcessPool):
    before = await pool.run(workers.pid)
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5):
            await pool.run(workers.sleep, 30)

    assert await pool.run(workers.pid) != before
    assert time.monotonic() - started < 10


async def test_aclose_is_idempotent():
    pool = ProcessPool('idle', processes=1, timeout=1)

    await pool.aclose()
    await pool.aclose()


def test_pool_errors_map_to_problems():
    with pytest.raises(ProcessingTimeout, match='slow'), pool_errors():
        raise PoolTimeout('slow')
    with pytest.raises(ProcessingFailed, match='died'), pool_errors():
        raise PoolFailed('died')
