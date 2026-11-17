import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Literal

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = 'x-request-id'

type LogFormat = Literal['text', 'json']

request_id: ContextVar[str | None] = ContextVar('request_id', default=None)

_VALID_REQUEST_ID = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')
# libraries (httpx, s3fs, playwright, pebble) are chatty below warning
_LIBRARY_LEVEL = logging.WARNING
# uvicorn configures these before importing the app; they keep the level of its --log-level
_UVICORN_LOGGERS = ('uvicorn', 'uvicorn.error', 'uvicorn.access')


class TextFormatter(logging.Formatter):
    """`2026-11-18T09:15:02.123Z INFO    papyrus.startup: message [request_id]`, UTC like the JSON lines"""

    def __init__(self) -> None:
        super().__init__('%(asctime)s %(levelname)-7s %(name)s: %(message)s')

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        return datetime.fromtimestamp(record.created, UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if rid := request_id.get():
            # the request id goes before a traceback, on the message line
            head, *rest = line.split('\n', 1)
            line = '\n'.join([f'{head} [{rid}]', *rest])
        return line


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            'time': datetime.fromtimestamp(record.created, UTC).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        if rid := request_id.get():
            entry['request_id'] = rid
        if record.exc_info:
            entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


class _Handler(logging.StreamHandler):
    """Marks the handler this module installs, so a second call replaces it instead of doubling lines"""


def configure_logging(namespace: str, level: int | str = logging.INFO, format: LogFormat = 'text') -> None:
    """One stderr handler on the root logger in `format`; `namespace` at `level`, libraries at warning

    Uvicorn's loggers lose their own handlers and propagate to it, keeping their level. Call it from the entrypoint
    after uvicorn configured logging; `create_app` never calls it, so tests and embedders keep their logging.
    """
    handler = _Handler(sys.stderr)
    handler.setFormatter(JsonFormatter() if format == 'json' else TextFormatter())
    root = logging.getLogger()
    for existing in [h for h in root.handlers if isinstance(h, _Handler)]:
        root.removeHandler(existing)
    root.addHandler(handler)
    # todo: per-logger levels (e.g. `info,httpx=debug`) once a library needs debugging in place
    root.setLevel(_LIBRARY_LEVEL)

    logger = logging.getLogger(namespace)
    logger.setLevel(level.upper() if isinstance(level, str) else level)
    logger.handlers.clear()
    logger.propagate = True
    for name in _UVICORN_LOGGERS:
        uvicorn = logging.getLogger(name)
        uvicorn.handlers.clear()
        uvicorn.propagate = True


class RequestIdMiddleware:
    """Echoes a valid incoming X-Request-ID or generates one, and binds it to log records"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        incoming = dict(scope['headers']).get(REQUEST_ID_HEADER.encode(), b'').decode('latin-1')
        rid = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id.set(rid)

        async def send_with_id(message: Message) -> None:
            if message['type'] == 'http.response.start':
                message['headers'] = [*message.get('headers', []), (REQUEST_ID_HEADER.encode(), rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id.reset(token)
