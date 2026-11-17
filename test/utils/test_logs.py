import logging
from collections.abc import Iterator

import pytest

from server.utils.logs import JsonFormatter, configure_logging, request_id


@pytest.fixture
def namespace() -> Iterator[str]:
    name = 'test-logs'
    yield name
    logging.getLogger(name).handlers.clear()


def test_json_formatter_binds_request_id():
    record = logging.LogRecord('papyrus', logging.INFO, __file__, 1, 'hello %s', ('world',), None)
    token = request_id.set('rid-1')
    try:
        line = JsonFormatter().format(record)
    finally:
        request_id.reset(token)

    assert '"message": "hello world"' in line
    assert '"request_id": "rid-1"' in line


def test_json_formatter_includes_exception():
    try:
        raise ValueError('boom')
    except ValueError:
        import sys

        record = logging.LogRecord('x', logging.ERROR, __file__, 1, 'failed', (), sys.exc_info())

    assert 'ValueError: boom' in JsonFormatter().format(record)


def test_configure_logging_is_idempotent_and_scoped(namespace: str):
    configure_logging(namespace)
    configure_logging(namespace)

    logger = logging.getLogger(namespace)
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, JsonFormatter)
    assert not logger.propagate
