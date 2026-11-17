import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, ClassVar

# fastapi is imported where used: pool workers import the error classes and would pay ~0.7s per process
if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

PROBLEM_JSON = 'application/problem+json'

_HTTP_CODES = {400: 'bad_request', 404: 'not_found', 405: 'method_not_allowed'}


class ProblemError(Exception):
    """RFC 9457 problem; subclasses set `status` and a stable `code`"""

    status: ClassVar[int] = 500
    code: ClassVar[str] = 'internal_error'

    def __init__(self, detail: str | None = None):
        # single positional arg keeps the error picklable across process pools
        super().__init__(detail)
        self.detail = detail

    def headers(self) -> dict[str, str]:
        return {}


class PayloadTooLarge(ProblemError):
    status, code = 413, 'payload_too_large'


class UnsupportedMediaType(ProblemError):
    status, code = 415, 'unsupported_media_type'


class ValidationFailed(ProblemError):
    status, code = 422, 'validation_error'


def validation_errors(errors: Any) -> list[dict[str, Any]]:
    """Keeps only stable, serializable fields (input and ctx may echo payloads or hold exceptions)"""
    return [{'loc': list(error['loc']), 'msg': error['msg'], 'type': error['type']} for error in errors]


def problem(
    status: int, code: str, detail: str | None = None, headers: dict[str, str] | None = None, **extra: Any
) -> JSONResponse:
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    body = {'type': 'about:blank', 'title': HTTPStatus(status).phrase, 'status': status, 'code': code}
    if detail:
        body['detail'] = detail
    body.update(extra)
    return JSONResponse(jsonable_encoder(body), status_code=status, headers=headers, media_type=PROBLEM_JSON)


def install_problem_handlers(app: FastAPI, logger: logging.Logger) -> None:
    """Renders every error as problem+json; unhandled errors are logged and never leak details"""
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException

    async def problem_error(_: Request, exc: ProblemError) -> JSONResponse:
        if exc.status >= 500:
            logger.warning('request failed: %s %s', exc.code, exc.detail)
        return problem(exc.status, exc.code, exc.detail, exc.headers())

    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(422, ValidationFailed.code, 'request validation failed', errors=validation_errors(exc.errors()))

    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return problem(exc.status_code, _HTTP_CODES.get(exc.status_code, 'http_error'), exc.detail, exc.headers)

    async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception('unhandled error', exc_info=exc)
        return problem(500, ProblemError.code)

    app.add_exception_handler(ProblemError, problem_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(Exception, unhandled_error)
