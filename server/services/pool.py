import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pebble

from server.errors import ProcessingFailed, ProcessingTimeout
from server.utils.problem import ProblemError

logger = logging.getLogger('papyrus.pool')


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
        """Worker functions must be top-level and should raise ProblemError, other errors are reported generically"""
        future = self._ensure().schedule(fn, args=args, timeout=self.timeout)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        except TimeoutError as e:
            raise ProcessingTimeout(f'{self.name} exceeded {self.timeout}s') from e
        except pebble.ProcessExpired as e:
            logger.warning('%s worker died with exit code %s', self.name, e.exitcode)
            raise ProcessingFailed(f'{self.name} worker died') from e
        except ProblemError:
            raise
        except Exception as e:
            logger.warning('%s task failed', self.name, exc_info=e)
            raise ProcessingFailed(f'{self.name} failed ({type(e).__name__})') from e

    def close(self) -> None:
        if self._pool is not None:
            self._pool.stop()
            self._pool.join()
            self._pool = None

    def _ensure(self) -> pebble.ProcessPool:
        if self._pool is None:
            logger.info('starting %s pool with %s processes', self.name, self.processes)
            self._pool = pebble.ProcessPool(max_workers=self.processes, max_tasks=self.max_tasks)
        return self._pool
