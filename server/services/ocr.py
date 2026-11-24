import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt, wait_random_exponential

from server.config import MistralConfig, OcrConfig
from server.errors import OcrFailed, OcrNotConfigured, OcrRateLimited, ProcessingTimeout
from server.ocr.base import OcrAdapter, OcrError, OcrTimeout, OcrTransient
from server.ocr.base import OcrRateLimited as BackendRateLimited
from server.services.base import Service
from server.utils.problem import ProblemError

TIMEOUT = 120
ATTEMPTS = 3
# longest wait between attempts; a backend asking for more fails at once
MAX_WAIT = 30

logger = logging.getLogger('papyrus.ocr')

# renders one page on the first attempt, its image is kept for the next ones
Page = Callable[[], Awaitable[Path]]


class OcrService(Service):
    """Slots, retries and error mapping over the adapter of the configured backend (adr/ocr.md)"""

    def __init__(
        self,
        config: OcrConfig | None,
        renders: int,
        adapter: OcrAdapter | None = None,
        timeout: float = TIMEOUT,
        backoff: float = 1,
    ):
        self._config = config
        self._adapter = adapter
        self._timeout = timeout
        self._backoff = wait_random_exponential(multiplier=backoff, max=MAX_WAIT)
        self._slots = asyncio.Semaphore(config.concurrency if config else 1)
        self._renders = asyncio.Semaphore(renders)

    @property
    def config(self) -> OcrConfig:
        if self._config is None:
            raise OcrNotConfigured('readMode ocr needs an OCR backend, this server has none')
        return self._config

    @property
    def adapter(self) -> OcrAdapter:
        if self._adapter is None:
            self._adapter = _create(self.config)
        return self._adapter

    async def warm(self) -> None:
        """Builds the client and never calls the backend: an OCR outage must not stop startup"""
        if self._config is not None:
            _ = self.adapter

    def rendering(self) -> AbstractAsyncContextManager[None]:
        """Held around every OCR render, so slow documents cannot take the whole shared render pool"""
        return self._renders

    async def read(self, number: int, page: Page) -> str:
        """Markdown of page `number`; its image is produced inside the slot, which bounds the renders waiting"""
        image: Path | None = None
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(ATTEMPTS),
                wait=self._wait,
                retry=retry_if_exception(_retryable),
                before_sleep=_log_retry(number),
                reraise=True,
            ):
                with attempt:
                    async with self._slots:
                        image = image or await page()
                        return await self._call(image)
        except OcrError as e:
            raise _problem(number, e) from e
        raise AssertionError('unreachable: the last attempt returns or raises')

    async def aclose(self) -> None:
        adapter, self._adapter = self._adapter, None
        if adapter is not None:
            await adapter.aclose()

    async def _call(self, image: Path) -> str:
        try:
            async with asyncio.timeout(self._timeout):
                return await self.adapter.read(image)
        except TimeoutError:
            raise OcrTimeout(f'the OCR backend exceeded {self._timeout:g}s') from None

    def _wait(self, state: RetryCallState) -> float:
        error = _error(state)
        if isinstance(error, BackendRateLimited) and error.retry_after is not None:
            return error.retry_after
        return self._backoff(state)


def _create(config: OcrConfig) -> OcrAdapter:
    # imported here: only the SDK of the configured backend is loaded
    if isinstance(config, MistralConfig):
        from server.ocr.mistral import MistralAdapter

        return MistralAdapter(config.model, config.api_key.get_secret_value(), config.url)
    from server.ocr.qwen import QwenAdapter

    api_key = config.api_key.get_secret_value() if config.api_key else None
    return QwenAdapter(config.model, config.url, api_key, config.extra_body)


def _retryable(error: BaseException) -> bool:
    if isinstance(error, BackendRateLimited):
        return error.retry_after is None or error.retry_after <= MAX_WAIT
    return isinstance(error, (OcrTransient, OcrTimeout))


def _error(state: RetryCallState) -> BaseException | None:
    return state.outcome.exception() if state.outcome else None


def _log_retry(number: int) -> Callable[[RetryCallState], None]:
    def log(state: RetryCallState) -> None:
        delay = state.next_action.sleep if state.next_action else 0
        logger.warning('page %s attempt %s: %s, retry in %.1fs', number, state.attempt_number, _error(state), delay)

    return log


def _problem(number: int, error: OcrError) -> ProblemError:
    detail = f'page {number}: {error}'
    if isinstance(error, BackendRateLimited):
        return OcrRateLimited(detail)
    if isinstance(error, OcrTimeout):
        return ProcessingTimeout(detail)
    return OcrFailed(detail)
