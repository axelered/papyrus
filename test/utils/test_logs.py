import logging
import sys
from collections.abc import Iterator

import pytest

from server.utils.logs import JsonFormatter, TextFormatter, configure_logging, request_id

NAMESPACE = 'test-logs'
_TOUCHED = ('', NAMESPACE, 'uvicorn', 'uvicorn.error', 'uvicorn.access')


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """configure_logging changes the root and uvicorn loggers process wide"""
    saved = {name: _state(logging.getLogger(name)) for name in _TOUCHED}
    yield
    for name, (handlers, level, propagate) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


def _state(logger: logging.Logger) -> tuple[list[logging.Handler], int, bool]:
    return list(logger.handlers), logger.level, logger.propagate


def _record(message: str = 'hello %s', args: tuple = ('world',), exc_info=None) -> logging.LogRecord:
    return logging.LogRecord('papyrus.test', logging.INFO, __file__, 1, message, args, exc_info)


def _bound(formatter: logging.Formatter, record: logging.LogRecord, rid: str = 'rid-1') -> str:
    token = request_id.set(rid)
    try:
        return formatter.format(record)
    finally:
        request_id.reset(token)


def _failed_record() -> logging.LogRecord:
    try:
        raise ValueError('boom')
    except ValueError:
        return _record('failed', (), sys.exc_info())


def test_json_formatter_binds_request_id():
    line = _bound(JsonFormatter(), _record())

    assert '"message": "hello world"' in line
    assert '"request_id": "rid-1"' in line


def test_json_formatter_includes_exception():
    assert 'ValueError: boom' in JsonFormatter().format(_failed_record())


def test_text_formatter_line():
    line = _bound(TextFormatter(), _record())

    assert line.endswith(' INFO    papyrus.test: hello world [rid-1]')
    assert line[:24].endswith('Z') and line[10] == 'T'


def test_text_formatter_keeps_request_id_on_the_message_line():
    head, *traceback = _bound(TextFormatter(), _failed_record()).split('\n')

    assert head.endswith('failed [rid-1]')
    assert 'ValueError: boom' in traceback[-1]


@pytest.mark.parametrize(('format', 'formatter'), [('text', TextFormatter), ('json', JsonFormatter)])
def test_configure_logging_installs_one_root_handler(restore_logging, format: str, formatter: type):
    configure_logging(NAMESPACE, 'debug', format)
    configure_logging(NAMESPACE, 'debug', format)

    root = logging.getLogger()
    ours = [handler for handler in root.handlers if isinstance(handler.formatter, (TextFormatter, JsonFormatter))]
    assert len(ours) == 1
    assert isinstance(ours[0].formatter, formatter)
    assert root.level == logging.WARNING


def test_configure_logging_levels_namespace_and_keeps_uvicorn_level(restore_logging):
    logging.getLogger('uvicorn.access').setLevel(logging.INFO)
    logging.getLogger('uvicorn.access').addHandler(logging.NullHandler())

    configure_logging(NAMESPACE, 'debug')

    namespace = logging.getLogger(NAMESPACE)
    assert (namespace.level, namespace.propagate, namespace.handlers) == (logging.DEBUG, True, [])
    access = logging.getLogger('uvicorn.access')
    assert (access.level, access.propagate, access.handlers) == (logging.INFO, True, [])
