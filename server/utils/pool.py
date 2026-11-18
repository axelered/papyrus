import asyncio
import logging
import multiprocessing
from collections.abc import Callable
from typing import Any, Self

import pebble

from server.utils.problem import ProblemError

logger = logging.getLogger('papyrus.pool')

# how often `start()` checks for initialized workers
READY_POLL = 0.05


class PoolTimeout(TimeoutError):
    """A task exceeded the pool timeout, its worker was killed"""


class PoolFailed(RuntimeError):
    """A worker died or a task raised something other than ProblemError"""


class ProcessPool:
    """pebble pool started by `start()` or lazily by the first task

    A timed out or cancelled task kills its worker, which pebble then replaces. `initializer` (top-level, no
    arguments; use `functools.partial`) runs in every new worker before its first task.
    """

    def __init__(
        self,
        name: str,
        processes: int,
        timeout: float,
        max_tasks: int = 0,
        initializer: Callable[[], None] | None = None,
    ):
        self.name = name
        self.processes = processes
        self.timeout = timeout
        self.max_tasks = max_tasks
        self.initializer = initializer
        self._pool: pebble.ProcessPool | None = None
        self._ready: Any = None
        self._failed: Any = None

    @property
    def started(self) -> bool:
        return self._pool is not None

    async def start(self) -> None:
        """Starts every worker and returns once each has run the initializer; PoolFailed if one failed"""
        # pebble spawns all workers on the first task
        await self.run(_noop)
        initialized = 0
        while initialized < self.processes:
            if self._failed.is_set():
                break
            if self._ready.acquire(block=False):
                initialized += 1
            else:
                await asyncio.sleep(READY_POLL)
        if self._failed.is_set():
            raise PoolFailed(f'{self.name} worker initialization failed, see the worker log')

    async def run[T](self, fn: Callable[..., T], *args: Any) -> T:
        """Worker functions must be top-level and should raise ProblemError, other errors become PoolFailed"""
        future = self._ensure().schedule(fn, args=args, timeout=self.timeout)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        except TimeoutError as e:
            raise PoolTimeout(f'{self.name} exceeded {self.timeout}s') from e
        except pebble.ProcessExpired as e:
            logger.warning('%s worker died with exit code %s', self.name, e.exitcode)
            raise PoolFailed(f'{self.name} worker died') from e
        except ProblemError:
            raise
        except Exception as e:
            logger.warning('%s task failed', self.name, exc_info=e)
            raise PoolFailed(f'{self.name} failed ({type(e).__name__})') from e

    async def aclose(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.stop()
            # join blocks until every worker has exited
            await asyncio.to_thread(pool.join)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _ensure(self) -> pebble.ProcessPool:
        if self._pool is None:
            logger.info('starting %s pool with %s processes', self.name, self.processes)
            context = multiprocessing.get_context()
            self._ready, self._failed = context.Semaphore(0), context.Event()
            self._pool = pebble.ProcessPool(
                max_workers=self.processes,
                max_tasks=self.max_tasks,
                initializer=_initialize,
                initargs=(self.name, self.initializer, self._ready, self._failed),
                context=context,
            )
        return self._pool


def _initialize(name: str, initializer: Callable[[], None] | None, ready: Any, failed: Any) -> None:
    """Never raises: a raising pebble initializer breaks the whole pool, so a recycled worker that fails here
    stays up and its tasks retry their own lazy setup"""
    try:
        if initializer is not None:
            initializer()
    except Exception:
        logger.exception('%s worker initialization failed', name)
        failed.set()
    finally:
        ready.release()


def _noop() -> None:
    pass
