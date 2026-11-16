import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = 'x-request-id'

request_id: ContextVar[str | None] = ContextVar('request_id', default=None)

_VALID_REQUEST_ID = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')


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


def configure_logging(level: int = logging.INFO) -> None:
    logger = logging.getLogger('papyrus')
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


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
