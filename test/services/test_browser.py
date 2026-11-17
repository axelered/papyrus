import asyncio
import os
import signal
import struct
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from server.config import BrowserConfig, HttpConfig
from server.errors import ProcessingFailed, ProcessingTimeout, SourceFetchFailed, SourceForbidden, UnsupportedUri
from server.services.browser import BrowserService
from server.types.common import MAX_IMAGE_SIDE, ImageFormat
from server.types.screenshot import ScreenshotOptions, WaitUntil

pytestmark = [pytest.mark.full, pytest.mark.anyio]

LOOPBACK = '127.0.0.1'
PAGE = '<html><body style="margin:0;background:#c00"><h1>papyrus</h1></body></html>'


class Site:
    """Minimal http server; every name resolves to it, so the Host header shows which name reached it"""

    def __init__(self):
        self.requests: list[tuple[str, str]] = []
        self.server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.sockets[0].getsockname()[1]

    def url(self, path: str, host: str = 'site.test') -> str:
        return f'http://{host}:{self.port}{path}'

    def hosts(self) -> set[str]:
        return {host.split(':')[0] for host, _ in self.requests}

    def paths(self) -> set[str]:
        return {path for _, path in self.requests}

    def route(self, path: str) -> tuple[int, dict[str, str], str]:
        port = self.port
        pages = {
            '/': PAGE,
            '/leaky': (
                f'<html><body><img src="http://evil.test:{port}/img.png"><img src="/ok.png"><script>'
                f'fetch("http://127.0.0.1:{port}/loopback").catch(() => {{}});'
                f'fetch("http://localhost:{port}/localhost").catch(() => {{}});'
                f'try {{ new WebSocket("ws://evil.test:{port}/ws") }} catch (e) {{}}'
                f'</script></body></html>'
            ),
            '/tall': '<html><body style="margin:0"><div style="height:20000px;background:#00c"></div></body></html>',
            '/loop': '<html><body><script>while (true) {}</script></body></html>',
            '/bomb': '<html><body><script>const a = []; while (true) { a.push(new Array(1e6).fill(1)) }</script>',
            '/escape': '<html><body><script>location.href = "file:///etc/passwd"</script></body></html>',
            '/marker': '<html><body>PAPYRUS-SECRET-7f3a</body></html>',
        }
        if path == '/redirect':
            return 302, {'Location': self.url('/', host='evil.test')}, ''
        if path == '/missing':
            return 404, {}, '<html><body>not here</body></html>'
        return 200, {}, pages.get(path, '')

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = (await reader.readuntil(b'\r\n\r\n')).decode('latin-1')
            path = head.split(' ', 2)[1]
            host = next((line[5:].strip() for line in head.split('\r\n') if line.lower().startswith('host:')), '')
            self.requests.append((host, path))
            if path == '/slow':
                await asyncio.sleep(30)
            status, headers, body = self.route(path)
            data = body.encode()
            lines = [
                f'HTTP/1.1 {status} X',
                'Content-Type: text/html',
                f'Content-Length: {len(data)}',
                'Connection: close',
            ]
            lines += [f'{name}: {value}' for name, value in headers.items()]
            writer.write(('\r\n'.join(lines) + '\r\n\r\n').encode() + data)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def site() -> AsyncIterator[Site]:
    running = Site()
    running.server = await asyncio.start_server(running.handle, LOOPBACK, 0)
    yield running
    running.server.close()


async def _loopback_dns(host: str, _: int) -> list[str]:
    if host in {'site.test', 'evil.test', 'localhost'}:
        return [LOOPBACK]
    raise OSError(f'unknown host {host}')


def _service(timeout: float = 20, recycle_after: int = 200, **http: object) -> BrowserService:
    config = HttpConfig.model_validate({'allowed_hosts': ['site.test'], 'allow_private': True, **http})
    return BrowserService(
        config, BrowserConfig(), 2, timeout=timeout, recycle_after=recycle_after, resolver=_loopback_dns
    )


@pytest.fixture
async def browser() -> AsyncIterator[BrowserService]:
    service = _service()
    yield service
    await service.aclose()


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data.startswith(b'\x89PNG\r\n\x1a\n')
    return struct.unpack('>II', data[16:24])


def _chromium_pids(main_only: bool) -> list[int]:
    pids = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / 'cmdline').read_bytes().split(b'\0')
        except OSError:
            continue
        if cmdline and b'headless_shell' in cmdline[0] and not (main_only and any(b'--type=' in a for a in cmdline)):
            pids.append(int(entry.name))
    return pids


def _zombies() -> int:
    count = 0
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            try:
                if (entry / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                    count += 1
            except (OSError, IndexError):
                pass
    return count


async def test_screenshot_viewport(browser: BrowserService, site: Site, tmp_path: Path):
    target = tmp_path / 'shot.png'

    capture = await browser.screenshot(site.url('/'), target, ScreenshotOptions(width=640, height=400, scale=2))

    assert _png_size(target) == (1280, 800)
    assert (capture.width, capture.height, capture.status) == (1280, 800, 200)
    assert capture.url == site.url('/')


@pytest.mark.parametrize(('format', 'magic'), [(ImageFormat.JPEG, b'\xff\xd8\xff'), (ImageFormat.WEBP, b'RIFF')])
async def test_screenshot_formats(browser: BrowserService, site: Site, tmp_path: Path, format, magic: bytes):
    target = tmp_path / f'shot.{format.extension}'

    await browser.screenshot(site.url('/'), target, ScreenshotOptions(format=format))

    assert target.read_bytes().startswith(magic)


async def test_full_page_is_cut_at_max_side(browser: BrowserService, site: Site, tmp_path: Path):
    target = tmp_path / 'tall.png'

    capture = await browser.screenshot(site.url('/tall'), target, ScreenshotOptions(width=800, full_page=True))

    assert _png_size(target) == (800, MAX_IMAGE_SIDE) == (capture.width, capture.height)


async def test_error_status_is_captured(browser: BrowserService, site: Site, tmp_path: Path):
    capture = await browser.screenshot(site.url('/missing'), tmp_path / 'm.png', ScreenshotOptions())

    assert capture.status == 404


async def test_all_page_traffic_goes_through_the_proxy(browser: BrowserService, site: Site, tmp_path: Path):
    options = ScreenshotOptions(wait_until=WaitUntil.NETWORKIDLE, delay=500)

    await browser.screenshot(site.url('/leaky'), tmp_path / 'l.png', options)

    assert '/ok.png' in site.paths()
    assert site.hosts() == {'site.test'}
    assert not site.paths() & {'/loopback', '/localhost', '/ws', '/img.png'}


@pytest.mark.parametrize('url', ['http://evil.test:1/', 'http://127.0.0.1:1/', 'http://localhost:1/'])
async def test_denied_source_never_reaches_the_browser(browser: BrowserService, tmp_path: Path, url: str):
    with pytest.raises(SourceForbidden):
        await browser.screenshot(url, tmp_path / 'x.png', ScreenshotOptions())
    assert browser._current is None


async def test_redirect_to_denied_host_is_forbidden(browser: BrowserService, site: Site, tmp_path: Path):
    with pytest.raises(SourceForbidden):
        await browser.screenshot(site.url('/redirect'), tmp_path / 'r.png', ScreenshotOptions())
    assert site.hosts() == {'site.test'}


async def test_rebinding_after_check_is_denied(site: Site, tmp_path: Path):
    answers = iter([['93.184.215.14'], [LOOPBACK]])

    async def rebinding(host: str, _: int) -> list[str]:
        return next(answers, [LOOPBACK])

    service = BrowserService(HttpConfig(), BrowserConfig(), 1, timeout=20, resolver=rebinding)
    try:
        with pytest.raises(SourceForbidden):
            await service.screenshot(f'http://rebind.test:{site.port}/', tmp_path / 'r.png', ScreenshotOptions())
    finally:
        await service.aclose()
    assert site.requests == []


async def test_unreachable_page_is_fetch_failure(browser: BrowserService, tmp_path: Path):
    with pytest.raises(SourceFetchFailed):
        await browser.screenshot('http://site.test:1/', tmp_path / 'u.png', ScreenshotOptions())


async def test_file_navigation_from_page_is_blocked(browser: BrowserService, site: Site, tmp_path: Path):
    capture = await browser.screenshot(site.url('/escape'), tmp_path / 'e.png', ScreenshotOptions(delay=500))

    assert capture.url.startswith('http://site.test')


@pytest.mark.parametrize('url', ['ftp://site.test/', 'file:///etc/passwd', 'site.test'])
async def test_non_http_sources_are_rejected(browser: BrowserService, tmp_path: Path, url: str):
    with pytest.raises(UnsupportedUri):
        await browser.screenshot(url, tmp_path / 'x.png', ScreenshotOptions())


async def test_http_disabled_forbids_everything(site: Site, tmp_path: Path):
    service = _service(enabled=False)
    with pytest.raises(SourceForbidden, match='disabled'):
        await service.screenshot(site.url('/'), tmp_path / 'x.png', ScreenshotOptions())
    await service.aclose()


async def test_infinite_loop_times_out_and_browser_survives(site: Site, tmp_path: Path):
    service = _service(timeout=3)
    try:
        with pytest.raises(ProcessingTimeout):
            await service.screenshot(site.url('/loop'), tmp_path / 'l.png', ScreenshotOptions())
        await service.screenshot(site.url('/'), tmp_path / 'ok.png', ScreenshotOptions())
    finally:
        await service.aclose()


async def test_heap_bomb_crashes_only_its_page(browser: BrowserService, site: Site, tmp_path: Path):
    with pytest.raises(ProcessingFailed):
        await browser.screenshot(site.url('/bomb'), tmp_path / 'b.png', ScreenshotOptions())

    await browser.screenshot(site.url('/'), tmp_path / 'ok.png', ScreenshotOptions())


async def test_killed_browser_fails_request_then_relaunches(browser: BrowserService, site: Site, tmp_path: Path):
    pending = asyncio.create_task(browser.screenshot(site.url('/slow'), tmp_path / 's.png', ScreenshotOptions()))
    for _ in range(100):
        if '/slow' in site.paths():
            break
        await asyncio.sleep(0.1)
    for pid in _chromium_pids(main_only=True):
        os.kill(pid, signal.SIGKILL)

    with pytest.raises(ProcessingFailed):
        await pending
    await browser.screenshot(site.url('/'), tmp_path / 'ok.png', ScreenshotOptions())


async def test_concurrent_requests_use_isolated_contexts(browser: BrowserService, site: Site, tmp_path: Path):
    shots = [browser.screenshot(site.url('/'), tmp_path / f'{i}.png', ScreenshotOptions()) for i in range(4)]

    captures = await asyncio.gather(*shots)

    assert all(capture.status == 200 for capture in captures)


async def test_recycle_replaces_browser_without_zombies(site: Site, tmp_path: Path):
    service = _service(recycle_after=2)
    try:
        seen = []
        for i in range(6):
            await service.screenshot(site.url('/'), tmp_path / f'{i}.png', ScreenshotOptions())
            assert service._current is not None
            seen.append(service._current)
        assert len({id(instance) for instance in seen}) == 3
        assert service._retired == set()
        assert len(_chromium_pids(main_only=True)) == 1
    finally:
        await service.aclose()
    await asyncio.sleep(1)
    assert _chromium_pids(main_only=False) == []
    assert _zombies() == 0


async def test_sandbox_failure_is_loud(site: Site, tmp_path: Path):
    """Under the default Docker seccomp profile user namespaces are blocked, so a sandboxed launch must fail"""
    config = HttpConfig.model_validate({'allowed_hosts': ['site.test'], 'allow_private': True})
    service = BrowserService(config, BrowserConfig(sandbox=True), 1, resolver=_loopback_dns)
    try:
        await service.screenshot(site.url('/'), tmp_path / 's.png', ScreenshotOptions())
    except ProcessingFailed as e:
        assert 'unavailable' in str(e)
    finally:
        await service.aclose()


# - - - Documents (adr/browser.md): served HTML must never reach local files or bypass the policy

SECRET = 'PAPYRUS-SECRET-7f3a'
DOCUMENT = 'http://document.invalid/page.html'


@pytest.fixture
def secret(tmp_path: Path) -> Path:
    path = tmp_path / 'secret.txt'
    path.write_text(SECRET, encoding='utf-8')
    return path


@pytest.fixture
def frame_texts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Text of every frame and the title after each capture, read through Playwright"""
    texts: list[str] = []
    original = BrowserService._shoot

    async def spy(self, context, page, navigation, target, options):
        capture = await original(self, context, page, navigation, target, options)
        for frame in page.frames:
            script = 'document.documentElement ? document.documentElement.innerText : ""'
            try:
                # error pages of blocked frames never get an execution context, evaluate would wait forever
                texts.append(await asyncio.wait_for(frame.evaluate(script), 2))
            except Exception:  # noqa: BLE001 a frame without readable content holds no leaked text either
                texts.append('')
        texts.append(await page.title())
        return capture

    monkeypatch.setattr(BrowserService, '_shoot', spy)
    return texts


def _document(tmp_path: Path, html: str) -> Path:
    path = tmp_path / 'page.html'
    path.write_text(html, encoding='utf-8')
    return path


async def test_document_cannot_embed_local_files(
    browser: BrowserService, site: Site, tmp_path: Path, secret: Path, frame_texts: list[str]
):
    html = (
        '<html><body><p>DOCUMENT-MARKER</p>'
        f'<iframe src="file://{secret}"></iframe><img src="file://{secret}"><embed src="file://{secret}">'
        f'<iframe src="{DOCUMENT}"></iframe>'
        '<img src="relative.png">'
        f'<img src="{site.url("/x.png", host="evil.test")}"><img src="{site.url("/ok.png")}">'
        f'<script>fetch("file://{secret}").then(r => r.text()).then(t => document.title = t).catch(() => {{}});'
        f'fetch("{DOCUMENT}").then(r => r.text()).then(t => document.title = t).catch(() => {{}});</script>'
        '</body></html>'
    )
    options = ScreenshotOptions(wait_until=WaitUntil.NETWORKIDLE, delay=500)

    capture = await browser.screenshot(DOCUMENT, tmp_path / 'd.png', options, document=_document(tmp_path, html))

    assert (capture.url, capture.status) == (DOCUMENT, 200)
    assert not any(SECRET in text for text in frame_texts)
    assert sum('DOCUMENT-MARKER' in text for text in frame_texts) == 1
    assert site.requests == [(f'site.test:{site.port}', '/ok.png')]


async def test_document_base_href_cannot_reach_local_files(
    browser: BrowserService, tmp_path: Path, secret: Path, frame_texts: list[str]
):
    relative = str(secret).lstrip('/')
    html = f'<html><head><base href="file:///"></head><body><iframe src="{relative}"></iframe><img src="{relative}">'

    await browser.screenshot(
        DOCUMENT, tmp_path / 'b.png', ScreenshotOptions(delay=300), document=_document(tmp_path, html)
    )

    assert not any(SECRET in text for text in frame_texts)


@pytest.mark.parametrize(
    'escape',
    [
        '<script>location.href = "file://{secret}"</script>',
        '<meta http-equiv="refresh" content="0;url=file://{secret}">',
        '<script>window.open("file://{secret}")</script>',
        '<script>location.href = "view-source:file://{secret}"</script>',
    ],
)
async def test_document_cannot_navigate_to_local_files(
    browser: BrowserService, tmp_path: Path, secret: Path, frame_texts: list[str], escape: str
):
    html = '<html><head>' + escape.format(secret=secret) + '</head><body>DOCUMENT-MARKER</body></html>'

    capture = await browser.screenshot(
        DOCUMENT, tmp_path / 'n.png', ScreenshotOptions(delay=1000), document=_document(tmp_path, html)
    )

    assert capture.url == DOCUMENT
    assert not any(SECRET in text for text in frame_texts)


async def test_document_at_a_fetched_url_resolves_relative_assets(browser: BrowserService, site: Site, tmp_path: Path):
    html = '<html><body><img src="/ok.png">DOCUMENT-MARKER</body></html>'
    options = ScreenshotOptions(wait_until=WaitUntil.NETWORKIDLE)

    capture = await browser.screenshot(
        site.url('/doc'), tmp_path / 'u.png', options, document=_document(tmp_path, html)
    )

    assert capture.url == site.url('/doc')
    assert site.paths() == {'/ok.png'}


async def test_document_skips_the_source_check(browser: BrowserService, tmp_path: Path):
    """The synthetic origin never resolves, yet the served document renders"""
    path = _document(tmp_path, '<html><body>DOCUMENT-MARKER</body></html>')

    capture = await browser.screenshot(DOCUMENT, tmp_path / 's.png', ScreenshotOptions(), document=path)

    assert capture.status == 200


async def test_frame_spy_sees_embedded_content(
    browser: BrowserService, site: Site, tmp_path: Path, frame_texts: list[str]
):
    """Positive control: an allowed iframe with the marker is visible to the spy, so a leak would be caught"""
    html = f'<html><body><iframe src="{site.url("/marker")}"></iframe></body></html>'
    options = ScreenshotOptions(wait_until=WaitUntil.NETWORKIDLE)

    await browser.screenshot(DOCUMENT, tmp_path / 'p.png', options, document=_document(tmp_path, html))

    assert any(SECRET in text for text in frame_texts)
