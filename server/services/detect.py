import asyncio
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.errors import ProcessingFailed
from server.formats import Format, FormatLabel, resolve

logger = logging.getLogger('papyrus.detect')


@dataclass(frozen=True)
class Document:
    path: Path
    format: Format


def normalize_extension(path: Path, value: Format) -> Path:
    """Renames the file so every downstream tool sees the detected format's extension (stem is kept)"""
    if value.label == FormatLabel.UNKNOWN or path.suffix.lower().lstrip('.') in value.extensions:
        return path
    return path.rename(path.with_name(f'{path.stem or path.name}.{value.extension}'))


class DetectService:
    """Inline service: magika runs in the API process, onnxruntime releases the GIL while predicting"""

    def __init__(self) -> None:
        self._magika: Any = None
        self._lock = threading.Lock()

    async def detect(self, path: Path) -> Document:
        try:
            label = await asyncio.to_thread(self._identify, path)
        except Exception as e:
            logger.warning('type detection failed for %s', path.name, exc_info=e)
            raise ProcessingFailed('type detection failed') from e
        value = resolve(label, path.suffix.lstrip('.'))
        logger.info('detected %s as %s (magika %s)', path.name, value.label, label)
        return Document(normalize_extension(path, value), value)

    def _identify(self, path: Path) -> str:
        result = self._model().identify_path(path)
        if not result.ok:
            raise OSError(f'magika status {result.status}')
        return str(result.output.label)

    def _model(self) -> Any:
        with self._lock:
            if self._magika is None:
                from magika import Magika

                self._magika = Magika()
            return self._magika
