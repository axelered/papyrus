from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from server.errors import ProcessingFailed, UnsupportedFormat
from server.services.base import Service, pool_errors
from server.types.common import FormatGroup, FormatLabel
from server.types.markdown import Chunk, ChunkingOptions, Extracted
from server.utils.pool import ProcessPool
from server.utils.problem import ProblemError
from server.utils.text import decode_text, fence
from server.utils.timing import stage

if TYPE_CHECKING:
    # detection loads magika, which pool workers must not import
    from server.services.detect import Document, Format

TIMEOUT = 120
MAX_TASKS = 100

CHUNKS = TypeAdapter(list[Chunk])

# formats markitdown reads; legacy office formats are modernized before (ConvertService.modernize)
FORMATS = frozenset(
    {
        FormatLabel.PDF,
        FormatLabel.DOCX,
        FormatLabel.PPTX,
        FormatLabel.XLSX,
        FormatLabel.XLS,
        FormatLabel.OUTLOOK,
        FormatLabel.HTML,
        FormatLabel.CSV,
        FormatLabel.TSV,
        FormatLabel.MARKDOWN,
        FormatLabel.TXT,
        FormatLabel.JSON,
        FormatLabel.JSONL,
        FormatLabel.XML,
        FormatLabel.RSS,
        FormatLabel.ATOM,
        FormatLabel.IPYNB,
        FormatLabel.EPUB,
        FormatLabel.ZIP,
    }
)

_converter: Any = None


def init_worker() -> None:
    """Pool initializer: markitdown and the chunking libraries loaded before the first task"""
    from server.services import chunking

    _ensure_converter()
    chunking.configure()
    import chonkie  # noqa: F401


def convert_to_markdown(
    source: str, target: str, chunking: ChunkingOptions | None = None, chunks_target: str | None = None
) -> str | None:
    """Pool worker: writes markdown to `target` (and chunks to `chunks_target`), returns the document title

    markitdown is imported here so only worker processes pay for it, and its errors are translated to ProblemError
    so the parent never has to unpickle library exception types.
    """
    from markitdown import FileConversionException, UnsupportedFormatException

    try:
        result = _ensure_converter().convert_local(source)
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


def chunk_file(source: str, chunks_target: str, chunking: ChunkingOptions) -> None:
    """Pool worker for markdown produced elsewhere (OCR)"""
    _chunk(Path(source).read_text(encoding='utf-8'), chunking, chunks_target)


def _ensure_converter() -> Any:
    global _converter
    if _converter is None:
        from markitdown import MarkItDown

        _converter = MarkItDown(enable_plugins=False)
    return _converter


def _write(markdown: str, target: str, chunking: ChunkingOptions | None, chunks_target: str | None) -> None:
    Path(target).write_text(markdown, encoding='utf-8', newline='')
    if chunking is not None and chunks_target is not None:
        _chunk(markdown, chunking, chunks_target)


def _chunk(markdown: str, chunking: ChunkingOptions, chunks_target: str) -> None:
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
        self._pool = ProcessPool('markdown', processes, timeout=TIMEOUT, max_tasks=MAX_TASKS, initializer=init_worker)

    async def warm(self) -> None:
        await self._pool.start()

    def accepts(self, value: 'Format') -> bool:
        """Explicit allowlist plus every format derived from magika that is text (code, configs, markup)"""
        return value.label in FORMATS or (value.derived and value.text)

    async def read(
        self,
        document: 'Document',
        target: Path,
        chunking: ChunkingOptions | None = None,
        chunks_target: Path | None = None,
    ) -> Extracted:
        """Writes the text stored in the file to `target`, chunked in the same worker call"""
        value = document.format
        with stage('markdown'):
            # only derived code is fenced; explicit formats grouped as code (ipynb) have a markitdown converter
            if value.derived and value.group == FormatGroup.CODE:
                await self.fence_code(document.path, target, value.label, chunking, chunks_target)
                return Extracted()
            return Extracted(title=await self.convert(document.path, target, chunking, chunks_target))

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

    async def chunk(self, source: Path, chunks_target: Path, chunking: ChunkingOptions) -> None:
        with pool_errors():
            await self._pool.run(chunk_file, str(source), str(chunks_target), chunking)

    async def aclose(self) -> None:
        await self._pool.aclose()


def _optional(path: Path | None) -> str | None:
    return str(path) if path is not None else None
