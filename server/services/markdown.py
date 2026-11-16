import re
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
    Path(target).write_text(result.markdown, encoding='utf-8', newline='')
    return result.title


def decode_text(raw: bytes) -> str:
    if not raw:
        return ''
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        pass
    # detection only for non utf-8 input, short single byte texts are ambiguous between code pages
    from charset_normalizer import from_bytes

    best = from_bytes(raw).best()
    return str(best) if best is not None else raw.decode('utf-8', errors='replace')


def fence(text: str, language: str) -> str:
    """One CommonMark fenced block; the fence is longer than any backtick run inside so content cannot break out"""
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    marker = '`' * max(3, longest + 1)
    body = text if not text or text.endswith('\n') else f'{text}\n'
    return f'{marker}{language}\n{body}{marker}\n'


def fence_code(source: str, target: str, language: str) -> None:
    """Pool worker for code sources: markitdown would pass them through, turning `#` comments into headings"""
    try:
        text = decode_text(Path(source).read_bytes())
    except Exception as e:
        raise ProcessingFailed(f'code decoding failed ({type(e).__name__})') from None
    Path(target).write_text(fence(text.replace('\r\n', '\n'), language), encoding='utf-8', newline='')


class MarkdownService:
    def __init__(self, processes: int):
        self._pool = ProcessPool('markdown', processes, timeout=TIMEOUT, max_tasks=MAX_TASKS)

    async def convert(self, source: Path, target: Path) -> str | None:
        return await self._pool.run(convert_to_markdown, str(source), str(target))

    async def fence_code(self, source: Path, target: Path, language: str) -> None:
        await self._pool.run(fence_code, str(source), str(target), language)

    def close(self) -> None:
        self._pool.close()
