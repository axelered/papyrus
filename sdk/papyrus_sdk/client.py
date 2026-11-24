import time
from types import TracebackType
from typing import Any, Literal, Self, TypeVar

import httpx

from papyrus_sdk import wire
from papyrus_sdk.errors import APIError, PapyrusError
from papyrus_sdk.models import (
    ApiModel,
    ChunkingOptions,
    Fit,
    ImageFormat,
    InspectResponse,
    MarkdownResponse,
    RenderResponse,
    ScreenshotResponse,
    StatusResponse,
    ThumbnailResponse,
    WaitUntil,
)

M = TypeVar('M', bound=ApiModel)

ImageFormatName = ImageFormat | Literal['png', 'jpeg', 'webp']
FitName = Fit | Literal['contain', 'cover']
WaitUntilName = WaitUntil | Literal['load', 'domcontentloaded', 'networkidle']
Timeout = float | httpx.Timeout


class Papyrus:
    """Synchronous client

    Every operation takes one document, in one of three forms:
    - positional: a URI when the string contains `://`, otherwise a local path; bytes and binary files are uploaded
    - `source=`: a URI, sent as is. Use it for strings coming from users or LLMs: it never reads the local disk
    - `file=`: an upload, as a path, bytes or binary file (always sent from its beginning)

    Options left to None take the server defaults.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: Timeout = wire.DEFAULT_TIMEOUT,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
    ):
        """`base_url` and `api_key` default to PAPYRUS_BASE_URL and PAPYRUS_API_KEY

        `timeout` must exceed the server's `limits.deadline`; it applies to `http_client` too, which stays open on
        close().
        """
        self._settings = wire.Settings.resolve(base_url, api_key, timeout, max_retries)
        self._owned = http_client is None
        self._http = http_client or httpx.Client(limits=wire.LIMITS)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def markdown(
        self,
        document: wire.File | None = None,
        /,
        *,
        source: str | None = None,
        file: wire.File | None = None,
        filename: str | None = None,
        chunking: ChunkingOptions | None = None,
        destination: str | None = None,
        timeout: Timeout | None = None,
    ) -> MarkdownResponse:
        """Markdown of a document or web page, split into chunks with `chunking`"""
        target = wire.resolve_input(document, source, file, filename, disk=True)
        options = {'chunking': chunking}
        return self._send(wire.op_call('/v1/markdown', MarkdownResponse, target, options, destination), timeout)

    def render(
        self,
        document: wire.File | None = None,
        /,
        *,
        source: str | None = None,
        file: wire.File | None = None,
        filename: str | None = None,
        page: int | None = None,
        width: int | None = None,
        height: int | None = None,
        dpi: int | None = None,
        format: ImageFormatName | None = None,
        destination: str | None = None,
        timeout: Timeout | None = None,
    ) -> RenderResponse:
        """One page or image frame at a readable size"""
        target = wire.resolve_input(document, source, file, filename, disk=True)
        options = {'page': page, 'width': width, 'height': height, 'dpi': dpi, 'format': format}
        return self._send(wire.op_call('/v1/render', RenderResponse, target, options, destination), timeout)

    def thumbnail(
        self,
        document: wire.File | None = None,
        /,
        *,
        source: str | None = None,
        file: wire.File | None = None,
        filename: str | None = None,
        page: int | None = None,
        width: int | None = None,
        height: int | None = None,
        fit: FitName | None = None,
        format: ImageFormatName | None = None,
        destination: str | None = None,
        timeout: Timeout | None = None,
    ) -> ThumbnailResponse:
        """One page, image or web page fitted into a box"""
        target = wire.resolve_input(document, source, file, filename, disk=True)
        options = {'page': page, 'width': width, 'height': height, 'fit': fit, 'format': format}
        return self._send(wire.op_call('/v1/thumbnail', ThumbnailResponse, target, options, destination), timeout)

    def screenshot(
        self,
        url: str,
        *,
        width: int | None = None,
        height: int | None = None,
        full_page: bool | None = None,
        scale: int | None = None,
        wait_until: WaitUntilName | None = None,
        delay: int | None = None,
        format: ImageFormatName | None = None,
        destination: str | None = None,
        timeout: Timeout | None = None,
    ) -> ScreenshotResponse:
        """A web page captured in headless Chromium"""
        options = {
            'width': width,
            'height': height,
            'full_page': full_page,
            'scale': scale,
            'wait_until': wait_until,
            'delay': delay,
            'format': format,
        }
        return self._send(wire.op_call('/v1/screenshot', ScreenshotResponse, url, options, destination), timeout)

    def inspect(
        self,
        document: wire.File | None = None,
        /,
        *,
        source: str | None = None,
        file: wire.File | None = None,
        filename: str | None = None,
        timeout: Timeout | None = None,
    ) -> InspectResponse:
        """Detected format and checks of a document, without processing it"""
        target = wire.resolve_input(document, source, file, filename, disk=True)
        return self._send(wire.op_call('/v1/inspect', InspectResponse, target, {}), timeout)

    def health(self) -> StatusResponse:
        """Raises unless the server is up; a public route, the API key is not verified"""
        return self._attempt(wire.HEALTH, {}, wire.timeout_of(wire.PROBE_TIMEOUT))

    def ready(self) -> bool:
        """False when the server is at capacity, raises when it is unreachable; the API key is not verified"""
        try:
            self._attempt(wire.READY, {}, wire.timeout_of(wire.PROBE_TIMEOUT))
        except APIError as e:
            if e.code == 'not_ready':
                return False
            raise
        return True

    def _send(self, call: wire.Call[M], timeout: Timeout | None) -> M:
        limit = self._settings.timeout if timeout is None else wire.timeout_of(timeout)
        attempt = 0
        with call.content() as content:
            while True:
                try:
                    return self._attempt(call, content, limit)
                except PapyrusError as e:
                    if attempt >= self._settings.max_retries or not wire.retryable(e):
                        raise
                    delay = wire.wait(attempt, e)
                time.sleep(delay)
                attempt += 1

    def _attempt(self, call: wire.Call[M], content: dict[str, Any], timeout: httpx.Timeout) -> M:
        settings = self._settings
        try:
            request = self._http.build_request(
                call.method, settings.base_url + call.path, headers=settings.headers(), timeout=timeout, **content
            )
            response = self._http.send(request, follow_redirects=False)
        except httpx.HTTPError as e:
            raise wire.connection_error(e) from e
        return wire.parse(call, response)
