import asyncio
import base64
import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from server.config import BrowserConfig, HttpConfig
from server.errors import ProcessingFailed, ProcessingTimeout, SourceFetchFailed, SourceForbidden, UnsupportedUri
from server.types.common import MAX_IMAGE_SIDE, ImageFormat
from server.types.screenshot import ScreenshotOptions
from server.utils.net import HostPolicy, Resolver, resolve_host
from server.utils.proxy import PinningProxy
from server.utils.timing import stage

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, CDPSession, Page, Playwright

logger = logging.getLogger('papyrus.browser')

TIMEOUT = 30
RECYCLE_AFTER = 200
HEAP_MB = 512
QUALITY = 85
_CLOSE_TIMEOUT = 5
_SCHEMES = frozenset({'http', 'https'})
# defense in depth over Chromium's own web to file: block for served documents
_DOCUMENT_SCHEMES = frozenset({'http', 'https', 'data', 'blob'})

ARGS = (
    # every connection goes through the pinning proxy: no QUIC, no WebRTC UDP outside the proxy
    '--disable-quic',
    '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
    '--webrtc-ip-handling-policy=disable_non_proxied_udp',
    # a memory bomb crashes its renderer instead of the container
    f'--js-flags=--max-old-space-size={HEAP_MB}',
    # container /dev/shm defaults to 64 MB
    '--disable-dev-shm-usage',
)


@dataclass(frozen=True)
class Capture:
    width: int
    height: int
    url: str
    status: int | None


@dataclass(eq=False)
class _Instance:
    browser: 'Browser'
    contexts: int = 0
    active: int = 0
    retired: bool = False


@dataclass
class _Navigation:
    """Per-request proxy rejections by host and the last main-frame navigation target"""

    url: str
    rejects: dict[str, int] = field(default_factory=dict)
    crashed: bool = False

    def reject(self, host: str, _: int, status: int) -> None:
        self.rejects[host.lower()] = status

    def rejected(self) -> int | None:
        return self.rejects.get((urlsplit(self.url).hostname or '').lower())


async def _deny_all(host: str, _: int) -> str:
    raise PermissionError(f'{host} is outside any request context')


class BrowserService:
    """One lazily launched headless Chromium, a fresh context and pinning proxy per request (adr/browser.md)"""

    def __init__(
        self,
        http: HttpConfig,
        config: BrowserConfig,
        concurrency: int,
        timeout: float = TIMEOUT,
        recycle_after: int = RECYCLE_AFTER,
        resolver: Resolver = resolve_host,
    ):
        self._http_enabled = http.enabled
        self._policy = HostPolicy(http.allowed_hosts, http.allow_private, resolver)
        self._sandbox = config.sandbox
        self._timeout = timeout
        self._recycle_after = recycle_after
        self._semaphore = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._deny: PinningProxy | None = None
        self._current: _Instance | None = None
        self._retired: set[_Instance] = set()

    async def screenshot(
        self, url: str, target: Path, options: ScreenshotOptions, document: Path | None = None
    ) -> Capture:
        """With `document`, its bytes answer the main navigation of `url` (adr/browser.md, Documents)

        The source check is then skipped: the fetcher already applied the policy, or `url` is a synthetic origin.
        """
        if document is None:
            await self._check_source(url)
            body = None
        else:
            body = await asyncio.to_thread(document.read_bytes)
        async with self._semaphore:
            with stage('browser.acquire'):
                instance = await self._acquire()
            try:
                async with asyncio.timeout(self._timeout):
                    return await self._capture(instance.browser, url, target, options, body)
            except TimeoutError:
                raise ProcessingTimeout(f'screenshot exceeded {self._timeout:g}s') from None
            finally:
                await self._release(instance)

    async def aclose(self) -> None:
        async with self._lock:
            instances = [*self._retired, *([self._current] if self._current else [])]
            self._current = None
            self._retired.clear()
            for instance in instances:
                await _close_browser(instance.browser)
            if self._playwright is not None:
                with contextlib.suppress(Exception):
                    await self._playwright.stop()
                self._playwright = None
            if self._deny is not None:
                await self._deny.aclose()
                self._deny = None

    async def _check_source(self, url: str) -> None:
        if not self._http_enabled:
            raise SourceForbidden('http(s) sources are disabled')
        parts = urlsplit(url)
        if parts.scheme not in _SCHEMES or not parts.hostname:
            raise UnsupportedUri(f'{url!r} is not an http(s) URL')
        try:
            await self._policy.check(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80))
        except PermissionError as e:
            raise SourceForbidden(str(e)) from None
        except OSError as e:
            raise SourceFetchFailed(f'cannot resolve host {parts.hostname!r}') from e

    # - - - Browser lifecycle

    async def _acquire(self) -> _Instance:
        async with self._lock:
            current = self._current
            if current is not None and not current.browser.is_connected():
                logger.warning('browser disconnected, relaunching')
                current = self._current = None
            if current is not None and current.contexts >= self._recycle_after:
                current.retired = True
                self._retired.add(current)
                if current.active == 0:
                    await self._close_retired(current)
                current = self._current = None
            if current is None:
                current = self._current = _Instance(await self._launch())
            current.contexts += 1
            current.active += 1
            return current

    async def _release(self, instance: _Instance) -> None:
        instance.active -= 1
        if instance.retired and instance.active == 0:
            await self._close_retired(instance)

    async def _close_retired(self, instance: _Instance) -> None:
        self._retired.discard(instance)
        await _close_browser(instance.browser)

    async def _launch(self) -> 'Browser':
        """Retries once with a fresh driver, so a crashed driver costs no request"""
        from playwright.async_api import Error

        for attempt in range(2):
            try:
                playwright, deny = await self._ensure_playwright()
                browser = await playwright.chromium.launch(
                    chromium_sandbox=self._sandbox,
                    args=list(ARGS),
                    # contexts bring their own proxy; anything outside a context hits the deny-all proxy
                    proxy={'server': f'http://127.0.0.1:{deny.port}'},
                )
            except Error as e:
                await self._reset_playwright()
                if attempt:
                    raise ProcessingFailed(f'browser is unavailable ({_first_line(e)})') from None
                continue
            logger.info('browser %s launched (sandbox %s)', browser.version, 'on' if self._sandbox else 'off')
            return browser
        raise AssertionError('unreachable')

    async def _ensure_playwright(self) -> tuple['Playwright', PinningProxy]:
        from playwright.async_api import async_playwright

        if self._deny is None:
            self._deny = PinningProxy(_deny_all, logger=logger)
            await self._deny.start()
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        return self._playwright, self._deny

    async def _reset_playwright(self) -> None:
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    # - - - Capture

    async def _capture(
        self, browser: 'Browser', url: str, target: Path, options: ScreenshotOptions, body: bytes | None
    ) -> Capture:
        from playwright.async_api import Error

        navigation = _Navigation(url)
        async with PinningProxy(self._policy.resolve, on_reject=navigation.reject, logger=logger) as proxy:
            try:
                with stage('browser.context'):
                    context = await browser.new_context(
                        viewport={'width': options.width, 'height': options.height},
                        device_scale_factor=options.scale,
                        service_workers='block',
                        accept_downloads=False,
                        proxy={'server': f'http://127.0.0.1:{proxy.port}'},
                    )
            except Error as e:
                raise ProcessingFailed(f'browser failed ({_first_line(e)})') from None
            try:
                page = await context.new_page()
                if body is not None:
                    await context.route(_any_url, _DocumentRoute(page, body).handle)
                return await self._shoot(context, page, navigation, target, options)
            except Error as e:
                if navigation.crashed:
                    raise ProcessingFailed('page crashed') from None
                raise ProcessingFailed(f'browser failed ({_first_line(e)})') from None
            finally:
                with stage('browser.close'), contextlib.suppress(Exception):
                    async with asyncio.timeout(_CLOSE_TIMEOUT):
                        await context.close()

    async def _shoot(
        self, context: 'BrowserContext', page: 'Page', navigation: _Navigation, target: Path, options: ScreenshotOptions
    ) -> Capture:
        from playwright.async_api import Error

        # the asyncio timeout around the capture fires first; this only stops Playwright from waiting forever
        context.set_default_timeout((self._timeout + _CLOSE_TIMEOUT) * 1000)
        page.on('crash', lambda _: setattr(navigation, 'crashed', True))
        page.on('request', lambda request: _track(navigation, page, request))
        try:
            with stage('browser.goto'):
                response = await page.goto(navigation.url, wait_until=options.wait_until.value)
        except Error as e:
            _raise_rejected(navigation)
            if navigation.crashed or page.is_closed():
                # the page or browser died, not the site: surfaces as processing_failed
                raise
            raise SourceFetchFailed(f'navigation to {navigation.url!r} failed ({_first_line(e)})') from None
        status = response.status if response else None
        if status in (403, 502):
            # a plain http navigation the proxy rejected arrives as the proxy's own response
            _raise_rejected(navigation)
        if options.delay:
            await asyncio.sleep(options.delay / 1000)

        # raw CDP because Playwright cannot encode webp; the capture resets Playwright's device metrics emulation,
        # so the clip carries the scale (layout already ran at the emulated ratio) and the page is discarded after
        session = await context.new_cdp_session(page)
        width, height = await _capture_size(session, options)
        params: dict[str, Any] = {
            'format': options.format.value,
            'captureBeyondViewport': options.full_page,
            'clip': {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': options.scale},
        }
        if options.format != ImageFormat.PNG:
            params['quality'] = QUALITY
        with stage('browser.capture'):
            shot = await session.send('Page.captureScreenshot', params)
        target.write_bytes(base64.b64decode(shot['data']))
        return Capture(width=width * options.scale, height=height * options.scale, url=page.url, status=status)


def _any_url(_: str) -> bool:
    # a predicate, not a glob: globs would neither match every scheme nor treat `?` in URLs literally
    return True


class _DocumentRoute:
    """Serves the document as the first main-frame navigation; replays and non web schemes are aborted"""

    def __init__(self, page: 'Page', body: bytes):
        self._page = page
        self._body = body
        self._served: str | None = None

    async def handle(self, route: Any) -> None:
        request = route.request
        if urlsplit(request.url).scheme not in _DOCUMENT_SCHEMES:
            await route.abort()
        elif self._served is None and request.is_navigation_request() and request.frame == self._page.main_frame:
            self._served = request.url
            await route.fulfill(status=200, content_type='text/html', body=self._body)
        elif request.url == self._served:
            await route.abort()
        else:
            await route.continue_()


def _track(navigation: _Navigation, page: 'Page', request: Any) -> None:
    if request.is_navigation_request() and request.frame == page.main_frame:
        navigation.url = request.url


def _raise_rejected(navigation: _Navigation) -> None:
    status = navigation.rejected()
    if status == 403:
        raise SourceForbidden(f'{navigation.url!r} is not allowed') from None
    if status == 502:
        raise SourceFetchFailed(f'{navigation.url!r} is unreachable') from None


async def _capture_size(session: 'CDPSession', options: ScreenshotOptions) -> tuple[int, int]:
    """CSS size of the capture; full page height is cut so the output stays within MAX_IMAGE_SIDE"""
    if not options.full_page:
        return options.width, options.height
    metrics = await session.send('Page.getLayoutMetrics')
    content = int(metrics['cssContentSize']['height'])
    return options.width, min(max(content, options.height), MAX_IMAGE_SIDE // options.scale)


async def _close_browser(browser: 'Browser') -> None:
    with contextlib.suppress(Exception):
        async with asyncio.timeout(_CLOSE_TIMEOUT):
            await browser.close()


def _first_line(error: Exception) -> str:
    return str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
