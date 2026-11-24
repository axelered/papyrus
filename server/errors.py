from typing import ClassVar

from server.utils.problem import PayloadTooLarge, ProblemError


class Unauthorized(ProblemError):
    """Missing or invalid bearer API key"""

    status, code = 401, 'unauthorized'

    def headers(self) -> dict[str, str]:
        return {'WWW-Authenticate': 'Bearer'}


class SourceForbidden(ProblemError):
    """http(s) source rejected by the fetch policy (disabled, private address, host not allowed)"""

    status, code = 403, 'source_forbidden'


class SourceNotFound(ProblemError):
    """Remote source or connector object does not exist"""

    status, code = 404, 'source_not_found'


class SourceTooLarge(PayloadTooLarge):
    """Remote source (http fetch, connector get) exceeds limits.max_file_size"""

    code = 'source_too_large'


class ImageTooLarge(SourceTooLarge):
    """Source image has more pixels than the decompression bomb guard allows"""

    code = 'image_too_large'


class TooManyPages(SourceTooLarge):
    """Document has more pages than ocr.max_pages"""

    code = 'too_many_pages'


class UnsupportedFormat(ProblemError):
    """Detected document format is not accepted by the operation"""

    status, code = 415, 'unsupported_format'


class UnsupportedUri(ProblemError):
    """URI is malformed or its scheme cannot be used where it appears"""

    status, code = 422, 'unsupported_uri'


class PageOutOfRange(ProblemError):
    """Requested page or frame is beyond the end of the document"""

    status, code = 422, 'page_out_of_range'


class UnknownConnector(ProblemError):
    """Connector URI names a connector missing from the config"""

    status, code = 422, 'unknown_connector'


class OcrNotConfigured(ProblemError):
    """readMode ocr without an OCR backend in the config"""

    status, code = 422, 'ocr_not_configured'


class ProcessingFailed(ProblemError):
    """A service failed on the document (worker crash, library error, detection failure)"""

    status, code = 500, 'processing_failed'


class SourceFetchFailed(ProblemError):
    """Remote source could not be fetched (network, upstream status, redirects)"""

    status, code = 502, 'source_fetch_failed'


class DestinationFailed(ProblemError):
    """Result could not be written to the destination connector"""

    status, code = 502, 'destination_failed'


class OcrFailed(ProblemError):
    """OCR backend failed after retries, rejected a page or truncated its answer"""

    status, code = 502, 'ocr_failed'


class OcrRateLimited(ProblemError):
    """OCR backend still rate limits after retries"""

    status, code = 503, 'ocr_rate_limited'
    retry_after: ClassVar[int] = 30

    def headers(self) -> dict[str, str]:
        return {'Retry-After': str(self.retry_after)}


class QueueFull(ProblemError):
    """Admission limit (workers.max_queue) is exhausted"""

    status, code = 503, 'queue_full'
    retry_after: ClassVar[int] = 2

    def headers(self) -> dict[str, str]:
        return {'Retry-After': str(self.retry_after)}


class NotReady(ProblemError):
    """Readiness probe while admission is exhausted; returned by `/ready`, never raised"""

    status, code = 503, 'not_ready'


class ProcessingTimeout(ProblemError):
    """A single service task exceeded its timeout"""

    status, code = 504, 'processing_timeout'


class DeadlineExceeded(ProblemError):
    """The whole request exceeded limits.deadline"""

    status, code = 504, 'deadline_exceeded'
