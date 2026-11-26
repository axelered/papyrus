import asyncio
import base64
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

_RATE_LIMITED = 429
_REQUEST_TIMEOUT = 408


class OcrError(Exception):
    """Messages reach the client: never the upstream body or URL, SDK exceptions carry both and are not chained"""


class OcrTransient(OcrError):
    """Backend or network failure, or an answer that cannot be read; may succeed on retry"""


class OcrThrottled(OcrError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class OcrTimeout(OcrError):
    """The backend did not answer in time"""


class OcrTruncated(OcrError):
    """The model stopped at its output limit, usually in a repetition loop"""


class OcrRejected(OcrError):
    """The backend refuses the request; a retry would get the same answer"""


class OcrAdapter(ABC):
    """One call to one model family, no policy: retries and limits belong to the service"""

    # rendering that suits the model, read by the caller; an adapter overrides them when its model needs other values
    dpi: ClassVar[int] = 200
    # Qwen3-VL cookbook maximum for markdown, about 4600 image tokens; Mistral accepts it
    max_pixels: ClassVar[int] = 4608 * 32 * 32

    @abstractmethod
    async def read(self, image: Path) -> str:
        """Markdown of one page, `image` is a JPEG; empty for a blank page; the service strips markers and spacing"""

    @abstractmethod
    async def aclose(self) -> None:
        """Releases the HTTP client"""


async def data_url(image: Path) -> str:
    content = await asyncio.to_thread(image.read_bytes)
    return f'data:image/jpeg;base64,{base64.b64encode(content).decode("ascii")}'


def status_error(status: int, headers: Mapping[str, str]) -> OcrError:
    message = f'the OCR backend answered HTTP {status}'
    if status == _RATE_LIMITED:
        return OcrThrottled(message, _retry_after(headers))
    if status == _REQUEST_TIMEOUT or status >= 500:
        return OcrTransient(message)
    return OcrRejected(message)


def _retry_after(headers: Mapping[str, str]) -> float | None:
    """Delay in seconds; the HTTP date form is ignored, backoff applies"""
    try:
        return max(0.0, float(headers['retry-after']))
    except (KeyError, ValueError):
        return None
