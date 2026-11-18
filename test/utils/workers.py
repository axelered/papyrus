import os
import time

from server.errors import UnsupportedFormat


def add(a: int, b: int) -> int:
    return a + b


def pid() -> int:
    return os.getpid()


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def raise_papyrus() -> None:
    raise UnsupportedFormat('nope')


def raise_generic() -> None:
    raise KeyError('internal')


def crash() -> None:
    os._exit(3)
