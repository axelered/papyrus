import re
import shutil
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Self

_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_MAX_NAME = 128
_FALLBACK_NAME = 'file'


def safe_filename(name: str) -> str:
    """Basename without path separators or reserved characters, keeping the extension for format detection"""
    base = re.split(r'[/\\]', name)[-1]
    base = _UNSAFE.sub('_', base).strip(' .')
    if len(base) > _MAX_NAME:
        stem, dot, suffix = base.rpartition('.')
        base = f'{stem[: _MAX_NAME - len(suffix) - 1]}.{suffix}' if dot and len(suffix) < 16 else base[:_MAX_NAME]
    return base or _FALLBACK_NAME


class Workspace:
    """Per-request temp directory; every file gets its own subdirectory so names never collide"""

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix='req-', dir=root))
        self._files = 0
        self._kept = False

    def new_file(self, filename: str) -> Path:
        self._files += 1
        directory = self.path / str(self._files)
        directory.mkdir()
        return directory / safe_filename(filename)

    def keep(self) -> None:
        """Hands cleanup over to the caller, e.g. a response background task that runs after streaming"""
        self._kept = True

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        if not self._kept or exc is not None:
            self.cleanup()
