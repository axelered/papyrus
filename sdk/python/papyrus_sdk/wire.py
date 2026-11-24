import io
import json
import os
import random
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Generic, TypeVar

import httpx
from pydantic import ValidationError
from pydantic.alias_generators import to_camel

from papyrus_sdk.errors import APIError, ConnectionFailed, InvalidResponse, PapyrusError, TimedOut
from papyrus_sdk.models import ApiModel, OpModel, Problem, StatusResponse
from papyrus_sdk.version import __version__

M = TypeVar('M', bound=ApiModel)

File = str | os.PathLike[str] | bytes | BinaryIO

# the server sends nothing until it is done, so reads wait longer than its default `limits.deadline` of 300 s
DEFAULT_TIMEOUT = 310.0
CONNECT_TIMEOUT = 5.0
PROBE_TIMEOUT = 5.0
# below the 5 s uvicorn keeps idle connections open: reusing one it just closed is a lost connection, never retried
LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=2.0)

MAX_WAIT = 30.0
_BACKOFF = 0.5
_PROBLEM = 'application/problem+json'


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str | None = field(repr=False)
    timeout: httpx.Timeout
    max_retries: int

    @classmethod
    def resolve(
        cls, base_url: str | None, api_key: str | None, timeout: float | httpx.Timeout, max_retries: int
    ) -> 'Settings':
        url = base_url or os.environ.get('PAPYRUS_BASE_URL')
        if not url:
            raise ValueError('pass base_url or set PAPYRUS_BASE_URL')
        if httpx.URL(url).scheme not in {'http', 'https'}:
            raise ValueError('base_url must start with http:// or https://')
        if max_retries < 0:
            raise ValueError('max_retries must not be negative')
        key = api_key or os.environ.get('PAPYRUS_API_KEY') or None
        if key is not None:
            _check_key(key)
        return cls(url.rstrip('/'), key, timeout_of(timeout), max_retries)

    def headers(self) -> dict[str, str]:
        headers = {'Accept': 'application/json', 'User-Agent': f'papyrus-sdk/{__version__}'}
        if self.api_key is not None:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers


def _check_key(key: str) -> None:
    """Rejected here without echoing the key: the HTTP layer would put an invalid header value in its error"""
    if ',' in key:
        raise ValueError('the API key contains a comma: PAPYRUS_API_KEY lists several keys on the server, set one here')
    if not key.isascii() or not key.isprintable() or key != key.strip():
        raise ValueError('the API key contains whitespace or characters not allowed in a header')


def timeout_of(value: float | httpx.Timeout) -> httpx.Timeout:
    if isinstance(value, httpx.Timeout):
        return value
    return httpx.Timeout(value, connect=min(value, CONNECT_TIMEOUT))


@dataclass(frozen=True)
class Upload:
    filename: str
    content: Path | bytes | BinaryIO

    @contextmanager
    def open(self) -> Iterator[bytes | BinaryIO]:
        if isinstance(self.content, Path):
            with self.content.open('rb') as file:
                yield file
        else:
            yield self.content


def resolve_input(
    document: File | None, source: str | None, file: File | None, filename: str | None, *, disk: bool
) -> str | Upload:
    """The source URI or the upload described by the three input forms; without `disk` only bytes are uploaded"""
    if sum(value is not None for value in (document, source, file)) != 1:
        raise TypeError('pass exactly one of: a positional document, source= or file=')
    if isinstance(document, str) and '://' in document:
        source = document
    if source is None:
        return _upload(document if file is None else file, filename, disk)
    if not isinstance(source, str):
        raise TypeError('source is a URI string, use file= for uploads')
    if filename is not None:
        raise TypeError('filename applies to uploads only')
    return source


def _upload(content: Any, filename: str | None, disk: bool) -> Upload:
    if isinstance(content, bytes):
        return Upload(filename or 'file', content)
    is_path = isinstance(content, (str, os.PathLike))
    if not is_path and not hasattr(content, 'read'):
        raise TypeError(f'cannot upload {type(content).__name__}, expected a path, bytes or a binary file')
    if not disk:
        raise NotImplementedError(
            'AsyncPapyrus uploads bytes only: pass the content as bytes, a URI as source=, or use Papyrus'
        )
    if is_path:
        path = Path(content)
        return Upload(filename or path.name, path)
    if isinstance(content, io.TextIOBase):
        raise TypeError('open the file in binary mode')
    # a file opened from a descriptor has an int as name
    name = getattr(content, 'name', None)
    filename = filename or (Path(name).name if isinstance(name, str) else 'file')
    seekable = getattr(content, 'seekable', None)
    if seekable is None or not seekable():
        # httpx declares a zero length for pipes, and a retry could not read them again
        content = content.read()
        if not isinstance(content, bytes):
            raise TypeError('open the file in binary mode')
    return Upload(filename, content)


@dataclass(frozen=True)
class Call(Generic[M]):
    """One request, independent of the client sending it"""

    method: str
    path: str
    model: type[M]
    body: dict[str, Any] | None = None
    upload: Upload | None = None

    @contextmanager
    def content(self) -> Iterator[dict[str, Any]]:
        """httpx arguments of the body; a path is open until the last attempt, httpx rewinds it on each one"""
        if self.body is None:
            yield {}
        elif self.upload is None:
            yield {'json': self.body}
        else:
            with self.upload.open() as file:
                yield {'data': {'metadata': json.dumps(self.body)}, 'files': {'file': (self.upload.filename, file)}}


HEALTH = Call('GET', '/health', StatusResponse)
READY = Call('GET', '/ready', StatusResponse)


def op_call(
    path: str, model: type[M], target: str | Upload, options: dict[str, Any], destination: str | None = None
) -> Call[M]:
    """Only options that are set are sent, defaults belong to the server"""
    body: dict[str, Any] = {
        'options': {to_camel(name): _wire(value) for name, value in options.items() if value is not None}
    }
    if path != '/v1/inspect':
        body['output'] = 'json'
    if destination is not None:
        body['destination'] = destination
    if isinstance(target, Upload):
        return Call('POST', path, model, body, target)
    return Call('POST', path, model, {'source': target, **body})


def _wire(value: Any) -> Any:
    if isinstance(value, ApiModel):
        return value.model_dump(mode='json', by_alias=True, exclude_unset=True)
    return value


def parse(call: Call[M], response: httpx.Response) -> M:
    request_id = response.headers.get('x-request-id')
    if not response.is_success:
        raise _api_error(response, request_id)
    try:
        parsed = call.model.model_validate_json(response.content)
    except ValidationError as e:
        raise InvalidResponse(
            f'{call.path} did not return a valid {call.model.__name__} (request {request_id}): {e}'
        ) from e
    if isinstance(parsed, OpModel):
        parsed.request_id = request_id
    return parsed


def _api_error(response: httpx.Response, request_id: str | None) -> APIError:
    problem = None
    if response.headers.get('content-type', '').startswith(_PROBLEM):
        try:
            problem = Problem.model_validate_json(response.content)
        except ValidationError:
            pass  # not a Papyrus problem, reported by status alone
    return APIError(
        response.status_code,
        problem.code if problem else None,
        problem.detail if problem else None,
        problem.errors if problem else None,
        request_id,
        _retry_after(response.headers.get('retry-after')),
    )


def _retry_after(value: str | None) -> float | None:
    # the server sends seconds, HTTP dates from proxies fall back to the backoff
    return float(value) if value is not None and value.isascii() and value.isdigit() else None


def connection_error(error: httpx.HTTPError) -> ConnectionFailed:
    kind = TimedOut if isinstance(error, httpx.TimeoutException) else ConnectionFailed
    return kind(f'{type(error).__name__}: {error}')


def retryable(error: PapyrusError) -> bool:
    """Only failures where the server did no work, documents are expensive to repeat (adr/sdk-python.md, Retries)"""
    if isinstance(error, APIError):
        return error.code == 'queue_full' or (error.code is None and error.status in {502, 503})
    return isinstance(error.__cause__, (httpx.ConnectError, httpx.ConnectTimeout))


def wait(attempt: int, error: PapyrusError) -> float:
    """Seconds before the next attempt; jittered, the server's `Retry-After` is the same for every client"""
    asked = error.retry_after if isinstance(error, APIError) else None
    return min(MAX_WAIT, max(asked or 0, _BACKOFF * 2.0**attempt) * random.uniform(1, 1.5))
