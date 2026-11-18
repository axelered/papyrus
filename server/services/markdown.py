from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from server.errors import ProcessingFailed, UnsupportedFormat
from server.services.base import Service, pool_errors
from server.types.markdown import Chunk, ChunkingOptions
from server.utils.pool import ProcessPool
from server.utils.problem import ProblemError
from server.utils.text import decode_text, fence

TIMEOUT = 120
MAX_TASKS = 100

CHUNKS = TypeAdapter(list[Chunk])

_converter: Any = None


def convert_to_markdown(
    source: str, target: str, chunking: ChunkingOptions | None = None, chunks_target: str | None = None
) -> str | None:
    """Pool worker: writes markdown to `target` (and chunks to `chunks_target`), returns the document title

    markitdown is imported here so only worker processes pay for it, and its errors are translated to ProblemError
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


def fence_code(
    source: str, target: str, language: str, chunking: ChunkingOptions | None = None, chunks_target: str | None = None
) -> None:
    """Pool worker for code sources: markitdown would pass them through, turning `#` comments into headings"""
    try:
        text = decode_text(Path(source).read_bytes())
    except Exception as e:
        raise ProcessingFailed(f'code decoding failed ({type(e).__name__})') from None
    _write(fence(text.replace('\r\n', '\n'), language), target, chunking, chunks_target)


def _write(markdown: str, target: str, chunking: ChunkingOptions | None, chunks_target: str | None) -> None:
    Path(target).write_text(markdown, encoding='utf-8', newline='')
    if chunking is None or chunks_target is None:
        return
    from server.services.chunking import chunk_markdown

    try:
        chunks = chunk_markdown(markdown, chunking)
    except ProblemError:
        raise
    except Exception as e:
        raise ProcessingFailed(f'chunking failed ({type(e).__name__})') from None
    Path(chunks_target).write_bytes(CHUNKS.dump_json(chunks))


class MarkdownService(Service):
    def __init__(self, processes: int):
        self._pool = ProcessPool('markdown', processes, timeout=TIMEOUT, max_tasks=MAX_TASKS)

    async def convert(
        self, source: Path, target: Path, chunking: ChunkingOptions | None = None, chunks_target: Path | None = None
    ) -> str | None:
        with pool_errors():
            return await self._pool.run(
                convert_to_markdown, str(source), str(target), chunking, _optional(chunks_target)
            )

    async def fence_code(
        self,
        source: Path,
        target: Path,
        language: str,
        chunking: ChunkingOptions | None = None,
        chunks_target: Path | None = None,
    ) -> None:
        with pool_errors():
            await self._pool.run(fence_code, str(source), str(target), language, chunking, _optional(chunks_target))

    async def aclose(self) -> None:
        await self._pool.aclose()


def _optional(path: Path | None) -> str | None:
    return str(path) if path is not None else None
