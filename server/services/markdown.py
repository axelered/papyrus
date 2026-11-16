import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from server.errors import PapyrusError, ProcessingFailed, UnsupportedFormat
from server.services.chunking.chunker import ChunkOptions
from server.services.fence import fence
from server.services.pool import ProcessPool

TIMEOUT = 120
MAX_TASKS = 100

_converter: Any = None


def convert_to_markdown(
    source: str, target: str, chunking: ChunkOptions | None = None, chunks_target: str | None = None
) -> str | None:
    """Pool worker: writes markdown to `target` (and chunks to `chunks_target`), returns the document title

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
    _write(result.markdown, target, chunking, chunks_target)
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


def fence_code(
    source: str, target: str, language: str, chunking: ChunkOptions | None = None, chunks_target: str | None = None
) -> None:
    """Pool worker for code sources: markitdown would pass them through, turning `#` comments into headings"""
    try:
        text = decode_text(Path(source).read_bytes())
    except Exception as e:
        raise ProcessingFailed(f'code decoding failed ({type(e).__name__})') from None
    _write(fence(text.replace('\r\n', '\n'), language), target, chunking, chunks_target)


def _write(markdown: str, target: str, chunking: ChunkOptions | None, chunks_target: str | None) -> None:
    Path(target).write_text(markdown, encoding='utf-8', newline='')
    if chunking is None or chunks_target is None:
        return
    from server.services.chunking.chunker import chunk_markdown

    try:
        chunks = chunk_markdown(markdown, chunking)
    except PapyrusError:
        raise
    except Exception as e:
        raise ProcessingFailed(f'chunking failed ({type(e).__name__})') from None
    Path(chunks_target).write_text(json.dumps([asdict(chunk) for chunk in chunks]), encoding='utf-8')


class MarkdownService:
    def __init__(self, processes: int):
        self._pool = ProcessPool('markdown', processes, timeout=TIMEOUT, max_tasks=MAX_TASKS)

    async def convert(
        self, source: Path, target: Path, chunking: ChunkOptions | None = None, chunks_target: Path | None = None
    ) -> str | None:
        return await self._pool.run(convert_to_markdown, str(source), str(target), chunking, _optional(chunks_target))

    async def fence_code(
        self,
        source: Path,
        target: Path,
        language: str,
        chunking: ChunkOptions | None = None,
        chunks_target: Path | None = None,
    ) -> None:
        await self._pool.run(fence_code, str(source), str(target), language, chunking, _optional(chunks_target))

    def close(self) -> None:
        self._pool.close()


def _optional(path: Path | None) -> str | None:
    return str(path) if path is not None else None
