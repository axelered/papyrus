from pathlib import Path
from typing import Any

from server.errors import ProcessingFailed, UnsupportedFormat
from server.services.pool import ProcessPool

TIMEOUT = 120
MAX_TASKS = 100

_converter: Any = None


def convert_to_markdown(source: str, target: str) -> str | None:
    """Pool worker: writes markdown to `target` and returns the document title

    markitdown is imported here so only worker processes pay for it, and its errors are translated to PapyrusError
    so the parent never has to unpickle library exception types.
    """
    global _converter
    from markitdown import FileConversionException, MarkItDown, UnsupportedFormatException

    if _converter is None:
        _converter = MarkItDown(enable_plugins=False)
    try:
        result = _converter.convert_local(source)
    except UnsupportedFormatException:
        raise UnsupportedFormat('file format is not supported for markdown extraction') from None
    except FileConversionException:
        raise UnsupportedFormat('file could not be converted, it may be corrupted or mislabeled') from None
    except Exception as e:
        raise ProcessingFailed(f'markdown extraction failed ({type(e).__name__})') from None
    Path(target).write_text(result.markdown, encoding='utf-8')
    return result.title


class MarkdownService:
    def __init__(self, processes: int):
        self._pool = ProcessPool('markdown', processes, timeout=TIMEOUT, max_tasks=MAX_TASKS)

    async def convert(self, source: Path, target: Path) -> str | None:
        return await self._pool.run(convert_to_markdown, str(source), str(target))

    def close(self) -> None:
        self._pool.close()
