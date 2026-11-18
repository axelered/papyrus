import asyncio
import logging
from collections.abc import Callable
from typing import Any, Self

import pebble

from server.utils.problem import ProblemError

logger = logging.getLogger('papyrus.pool')


class PoolTimeout(TimeoutError):
    """A task exceeded the pool timeout, its worker was killed"""


class PoolFailed(RuntimeError):
    """A worker died or a task raised something other than ProblemError"""


class ProcessPool:
    """Lazily started pebble pool: a timed out or cancelled task kills its worker, which pebble then replaces"""

    def __init__(self, name: str, processes: int, timeout: float, max_tasks: int = 0):
        self.name = name
        self.processes = processes
        self.timeout = timeout
        self.max_tasks = max_tasks
        self._pool: pebble.ProcessPool | None = None

    @property
    def started(self) -> bool:
        return self._pool is not None

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
            self._pool = pebble.ProcessPool(max_workers=self.processes, max_tasks=self.max_tasks)
        return self._pool
