from collections.abc import Iterator
from contextlib import contextmanager

from server.errors import QueueFull


class Admission:
    """Non-blocking in-flight request limit; a counter is enough since it is only touched from the event loop"""

    def __init__(self, limit: int):
        self.limit = limit
        self.in_flight = 0

    @property
    def available(self) -> int:
        return self.limit - self.in_flight

    @contextmanager
    def slot(self) -> Iterator[None]:
        if self.in_flight >= self.limit:
            raise QueueFull(f'{self.in_flight} requests in flight')
        self.in_flight += 1
        try:
            yield
        finally:
            self.in_flight -= 1
