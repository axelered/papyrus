import logging
from http import HTTPStatus
from typing import Any, ClassVar

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger('papyrus.errors')

PROBLEM_JSON = 'application/problem+json'


class PapyrusError(Exception):
    status: ClassVar[int] = 500
    code: ClassVar[str] = 'internal_error'

    def __init__(self, detail: str | None = None):
        # single positional arg keeps the error picklable across process pools
        super().__init__(detail)
        self.detail = detail

    def headers(self) -> dict[str, str]:
        return {}


class Unauthorized(PapyrusError):
    status, code = 401, 'unauthorized'

    def headers(self) -> dict[str, str]:
        return {'WWW-Authenticate': 'Bearer'}


class SourceForbidden(PapyrusError):
    status, code = 403, 'source_forbidden'


class SourceNotFound(PapyrusError):
    status, code = 404, 'source_not_found'


class SourceTooLarge(PapyrusError):
    status, code = 413, 'source_too_large'


class UnsupportedMediaType(PapyrusError):
    status, code = 415, 'unsupported_media_type'


class UnsupportedFormat(PapyrusError):
    status, code = 415, 'unsupported_format'


class ValidationFailed(PapyrusError):
    status, code = 422, 'validation_error'


class UnsupportedUri(PapyrusError):
    status, code = 422, 'unsupported_uri'


class UnknownConnector(PapyrusError):
    status, code = 422, 'unknown_connector'


class ProcessingFailed(PapyrusError):
    status, code = 500, 'processing_failed'


class SourceFetchFailed(PapyrusError):
    status, code = 502, 'source_fetch_failed'


class DestinationFailed(PapyrusError):
    status, code = 502, 'destination_failed'


class QueueFull(PapyrusError):
    status, code = 503, 'queue_full'
    retry_after: ClassVar[int] = 2

    def headers(self) -> dict[str, str]:
        return {'Retry-After': str(self.retry_after)}


class ProcessingTimeout(PapyrusError):
    status, code = 504, 'processing_timeout'


class DeadlineExceeded(PapyrusError):
    status, code = 504, 'deadline_exceeded'


_HTTP_CODES = {400: 'bad_request', 404: 'not_found', 405: 'method_not_allowed'}


def validation_errors(errors: Any) -> list[dict[str, Any]]:
    """Keeps only stable, serializable fields (input and ctx may echo payloads or hold exceptions)"""
    return [{'loc': list(error['loc']), 'msg': error['msg'], 'type': error['type']} for error in errors]


def problem(
    status: int, code: str, detail: str | None = None, headers: dict[str, str] | None = None, **extra: Any
) -> JSONResponse:
    body = {'type': 'about:blank', 'title': HTTPStatus(status).phrase, 'status': status, 'code': code}
    if detail:
        body['detail'] = detail
    body.update(extra)
    return JSONResponse(jsonable_encoder(body), status_code=status, headers=headers, media_type=PROBLEM_JSON)


async def _papyrus_error(_: Request, exc: PapyrusError) -> JSONResponse:
    if exc.status >= 500:
        logger.warning('request failed: %s %s', exc.code, exc.detail)
    return problem(exc.status, exc.code, exc.detail, exc.headers())


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return problem(422, ValidationFailed.code, 'request validation failed', errors=validation_errors(exc.errors()))


async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return problem(exc.status_code, _HTTP_CODES.get(exc.status_code, 'http_error'), exc.detail, exc.headers)


async def _unhandled_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception('unhandled error', exc_info=exc)
    return problem(500, PapyrusError.code)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PapyrusError, _papyrus_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
