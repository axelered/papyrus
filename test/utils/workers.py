import os
import time
from pathlib import Path

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


def mark(directory: str) -> None:
    """Initializer: one file per initialized worker"""
    (Path(directory) / str(os.getpid())).touch()


def fail_init() -> None:
    raise RuntimeError('init failed')
