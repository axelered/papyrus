"""Cold and hot latency of every service operation, in process and without HTTP (adr/performance.md)

    docker exec papyrus-dev python -m scripts.bench

Each operation gets a fresh service with one worker: `cold` is the first call (pool spawn, worker imports, lazy
libraries, browser launch), `hot` the median of the next calls on the same worker. Samples come from
`test/fixtures`, which the dev container mounts. Prints a markdown table for the ADR.

Server modules are imported inside functions only: spawn and forkserver import `__main__` into every pool worker,
module level imports here would warm the workers and hide the cold cost.
"""

import argparse
import asyncio
import multiprocessing
import os
import platform
import statistics
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from multiprocessing import forkserver
from pathlib import Path
from typing import Any

HTML = (
    b'<!doctype html><html><head><title>Benchmark</title></head><body style="font-family:sans-serif">'
    b'<h1>Quarterly report</h1>' + b'<p>Revenue grew across every region in the quarter.</p>' * 40 + b'</body></html>'
)
# never resolves: the browser serves the document itself (adr/browser.md, Documents)
DOCUMENT_URL = 'http://document.invalid/page.html'

type Run = Callable[[Any, Path, Path], Awaitable[Any]]


@dataclass(frozen=True)
class Op:
    service: str
    name: str
    make: Callable[[Path], Any]
    run: Run
    source: Callable[[], bytes]
    suffix: str
    enabled: bool = True


@dataclass(frozen=True)
class Row:
    op: Op
    cold: float
    hot: float | None
    error: str | None = None


def ops() -> list[Op]:
    from server.services import lok
    from server.types.markdown import ChunkingOptions
    from test.fixtures import samples

    def png() -> bytes:
        return samples.image('PNG', size=(1600, 1200))

    def pdf() -> bytes:
        return samples.pdf_pages((595, 842))

    office = lok.available()
    return [
        Op('detect', 'png', _detect, lambda s, src, _: s.detect(src), png, '.png'),
        Op('render', 'png 1600x1200 to webp 256', _render, _page, png, '.png'),
        Op('render', 'pdf A4 to webp 256', _render, _page, pdf, '.pdf'),
        Op('markdown', 'html', _markdown, _to_markdown(None), lambda: HTML, '.html'),
        Op('markdown', 'html with chunking', _markdown, _to_markdown(ChunkingOptions()), lambda: HTML, '.html'),
        Op('markdown', 'docx', _markdown, _to_markdown(None), samples.docx, '.docx'),
        Op('convert', 'docx page 1 to pdf', _convert, _to_pdf, samples.docx, '.docx', office),
        Op('browser', 'html screenshot 1280x800', _browser, _screenshot, lambda: HTML, '.html'),
    ]


# - - - Services


def _detect(_: Path) -> Any:
    from server.services.detect import DetectService

    return DetectService()


def _render(_: Path) -> Any:
    from server.services.render import RenderService

    return RenderService(processes=1)


def _markdown(_: Path) -> Any:
    from server.services.markdown import MarkdownService

    return MarkdownService(processes=1)


def _convert(work: Path) -> Any:
    from server.services.convert import ConvertService

    return ConvertService(processes=1, profiles=work / 'lo-profiles')


def _browser(_: Path) -> Any:
    from server.config import BrowserConfig, HttpConfig
    from server.services.browser import BrowserService

    return BrowserService(HttpConfig(), BrowserConfig(), concurrency=1)


async def _close(service: Any) -> None:
    if hasattr(service, 'aclose'):
        await service.aclose()
    elif hasattr(service, 'close'):
        await asyncio.to_thread(service.close)


# - - - Operations


async def _page(service: Any, source: Path, out: Path) -> Any:
    from server.services.render import RenderSpec
    from server.types.common import ImageFormat
    from server.types.render import Fit

    spec = RenderSpec(1, 256, 256, None, Fit.CONTAIN, ImageFormat.WEBP)
    return await service.page(source, out / 'thumbnail.webp', spec)


def _to_markdown(chunking: Any) -> Run:
    async def run(service: Any, source: Path, out: Path) -> Any:
        chunks = out / 'chunks.json' if chunking else None
        return await service.convert(source, out / 'out.md', chunking, chunks)

    return run


async def _to_pdf(service: Any, source: Path, out: Path) -> None:
    from server.types.convert import ConvertFormat

    await service.convert(source, out / 'out.pdf', ConvertFormat.PDF, page=1)


async def _screenshot(service: Any, source: Path, out: Path) -> Any:
    from server.types.screenshot import ScreenshotOptions

    return await service.screenshot(DOCUMENT_URL, out / 'shot.png', ScreenshotOptions(), document=source)


# - - - Measurement


async def _timed(op: Op, service: Any, work: Path, index: int) -> float:
    """Inputs are written before the clock starts; every call gets its own folder, detect renames its source"""
    out = work / str(index)
    out.mkdir()
    source = out / f'source{op.suffix}'
    source.write_bytes(op.source())
    start = time.perf_counter()
    await op.run(service, source, out)
    return (time.perf_counter() - start) * 1000


async def measure(op: Op, hot: int, root: Path) -> Row:
    work = Path(tempfile.mkdtemp(prefix=f'{op.service}-', dir=root))
    service = op.make(work)
    try:
        cold = await _timed(op, service, work, 0)
        hots = [await _timed(op, service, work, index) for index in range(1, hot + 1)]
        return Row(op, cold, statistics.median(hots) if hots else None)
    except Exception as e:
        return Row(op, 0, None, f'{type(e).__name__}: {e}'.splitlines()[0][:100])
    finally:
        await _close(service)


def table(rows: list[Row], hot: int) -> str:
    lines = [
        f'Python {platform.python_version()}, {platform.system()} {platform.machine()}, {os.cpu_count()} CPUs, '
        f'1 worker per service, hot = median of {hot}',
        '',
        '| Service | Operation | Cold (ms) | Hot (ms) |',
        '|---|---|---:|---:|',
    ]
    for row in rows:
        if row.error:
            lines.append(f'| {row.op.service} | {row.op.name} | failed: {row.error} | |')
            continue
        hot_ms = f'{row.hot:.0f}' if row.hot is not None else '-'
        lines.append(f'| {row.op.service} | {row.op.name} | {row.cold:.0f} | {hot_ms} |')
    return '\n'.join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description='Cold and hot latency per service operation')
    parser.add_argument('--hot', type=int, default=3, help='calls after the cold one, reported as their median')
    parser.add_argument('--only', default='', help='run only services whose name contains this text')
    args = parser.parse_args()

    selected = [op for op in ops() if args.only in op.service]
    if multiprocessing.get_start_method() == 'forkserver':
        # booted once per server process, so it is kept out of the first pool's cold time
        forkserver.ensure_running()
    rows = []
    with tempfile.TemporaryDirectory(prefix='papyrus-bench-') as root:
        for op in selected:
            if not op.enabled:
                continue
            print(f'running {op.service}: {op.name}', file=sys.stderr, flush=True)
            rows.append(await measure(op, args.hot, Path(root)))
    print(table(rows, args.hot))
    skipped = [f'{op.service} {op.name}' for op in selected if not op.enabled]
    if skipped:
        print(f'\nskipped, LibreOffice not installed: {", ".join(skipped)}')
    return 1 if any(row.error for row in rows) else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
