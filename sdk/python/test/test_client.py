"""Requests, configuration, errors and retries of the sync client over a mock transport"""

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from papyrus_sdk import APIError, ChunkingOptions, ConnectionFailed, InvalidResponse, Papyrus, TimedOut, __version__

from test.fixtures.sdk import Server, problem

INSPECTED = {'source': {'uri': 'file://file'}, 'options': {}, 'checks': {}}
URL = 'https://axelered.com'


def _papyrus(handler: Server, **options: Any) -> Papyrus:
    options = {'base_url': 'http://papyrus', 'api_key': 'dev', **options}
    return Papyrus(**options, http_client=httpx.Client(transport=httpx.MockTransport(handler)))


OK = httpx.Response(200, json=INSPECTED)


# - - - requests


def test_request_headers_and_url():
    server = Server(OK)

    _papyrus(server, base_url='http://papyrus/prefix/').inspect(URL)

    request = server.requests[0]
    assert str(request.url) == 'http://papyrus/prefix/v1/inspect'
    assert request.headers['authorization'] == 'Bearer dev'
    assert request.headers['user-agent'] == f'papyrus-sdk/{__version__}'


def test_only_set_options_are_sent():
    server = Server(httpx.Response(500))

    for call in (
        lambda c: c.inspect(URL),
        lambda c: c.markdown(URL, chunking=ChunkingOptions(max_tokens=99)),
        lambda c: c.screenshot(URL, full_page=False, destination='s3://prod/a.png'),
    ):
        with pytest.raises(APIError):
            call(_papyrus(server))

    assert [json.loads(request.content) for request in server.requests] == [
        {'source': URL, 'options': {}},
        {'source': URL, 'options': {'chunking': {'maxTokens': 99}}, 'output': 'json'},
        {'source': URL, 'options': {'fullPage': False}, 'output': 'json', 'destination': 's3://prod/a.png'},
    ]


def test_read_timeout_outlasts_the_server_deadline():
    server = Server(OK)
    client = _papyrus(server)

    client.inspect(URL)
    client.inspect(URL, timeout=2)
    client.ready()

    timeouts = [request.extensions['timeout'] for request in server.requests]
    assert [(t['connect'], t['read']) for t in timeouts] == [(5, 310), (2, 2), (5, 5)]


# - - - input


@pytest.mark.parametrize(
    ('args', 'kwargs', 'message'),
    [
        ((), {}, 'exactly one'),
        ((URL,), {'source': URL}, 'exactly one'),
        ((), {'source': URL, 'file': b'x'}, 'exactly one'),
        ((), {'source': Path('a.pdf')}, 'URI string'),
        ((URL,), {'filename': 'a.pdf'}, 'uploads only'),
        ((), {'source': URL, 'filename': 'a.pdf'}, 'uploads only'),
        ((42,), {}, 'cannot upload int'),
    ],
)
def test_invalid_input_sends_nothing(args: tuple[Any, ...], kwargs: dict[str, Any], message: str):
    server = Server(OK)

    with pytest.raises(TypeError, match=message):
        _papyrus(server).markdown(*args, **kwargs)
    assert server.requests == []


def test_text_files_are_rejected(tmp_path: Path):
    path = tmp_path / 'a.txt'
    path.write_text('x')

    with path.open() as file, pytest.raises(TypeError, match='binary mode'):
        _papyrus(Server(OK)).markdown(file)


def test_string_without_scheme_is_a_local_path():
    with pytest.raises(FileNotFoundError):
        _papyrus(Server(OK)).markdown('axelered.com')


def test_file_from_a_descriptor_has_no_name(tmp_path: Path):
    path = tmp_path / 'a.txt'
    path.write_bytes(b'x')
    server = Server(OK)

    with open(os.open(path, os.O_RDONLY), 'rb') as file:
        _papyrus(server).inspect(file)

    assert b'filename="file"' in server.requests[0].content


# - - - configuration


def test_settings_come_from_the_environment(sdk_env: pytest.MonkeyPatch):
    sdk_env.setenv('PAPYRUS_BASE_URL', 'http://from-env')
    sdk_env.setenv('PAPYRUS_API_KEY', 'env-key')
    server = Server(OK)

    _papyrus(server, base_url=None, api_key=None).inspect(URL)

    assert server.requests[0].url.host == 'from-env'
    assert server.requests[0].headers['authorization'] == 'Bearer env-key'


@pytest.mark.usefixtures('sdk_env')
def test_no_key_sends_no_authorization():
    server = Server(OK)

    _papyrus(server, api_key=None).inspect(URL)

    assert 'authorization' not in server.requests[0].headers


@pytest.mark.usefixtures('sdk_env')
@pytest.mark.parametrize(
    ('options', 'message'),
    [
        ({'base_url': None}, 'PAPYRUS_BASE_URL'),
        ({'base_url': 'papyrus:8000'}, 'http://'),
        ({'max_retries': -1}, 'max_retries'),
        ({'api_key': 'secret-a,secret-b'}, 'comma'),
        ({'api_key': 'secret\n'}, 'whitespace'),
        ({'api_key': 'secrèt'}, 'characters'),
    ],
)
def test_invalid_settings(options: dict[str, Any], message: str):
    with pytest.raises(ValueError, match=message) as error:
        _papyrus(Server(OK), **options)

    assert 'secr' not in str(error.value)


def test_key_is_never_shown():
    client = _papyrus(Server(problem(401, 'unauthorized')), api_key='hidden')

    with pytest.raises(APIError) as error:
        client.inspect(URL)

    assert 'hidden' not in f'{client!r} {client._settings!r} {error.value!r}'


def test_only_its_own_http_client_is_closed():
    external = httpx.Client()
    with Papyrus('http://papyrus', http_client=external):
        pass
    with Papyrus('http://papyrus') as client:
        owned = client._http

    assert not external.is_closed
    assert owned.is_closed


# - - - errors


def test_redirects_are_not_followed():
    server = Server(httpx.Response(302, headers={'location': 'http://elsewhere/'}))
    client = Papyrus(
        'http://papyrus', 'dev', http_client=httpx.Client(transport=httpx.MockTransport(server), follow_redirects=True)
    )

    with pytest.raises(APIError) as error:
        client.inspect(URL)

    assert error.value.status == 302
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    'response',
    [
        httpx.Response(413, text='<html>nginx</html>'),
        httpx.Response(413, text='{', headers={'content-type': 'application/problem+json'}),
        httpx.Response(413, json={'title': 'x'}, headers={'content-type': 'application/problem+json'}),
    ],
)
def test_errors_that_are_not_papyrus_problems_have_no_code(response: httpx.Response):
    with pytest.raises(APIError) as error:
        _papyrus(Server(response)).inspect(URL)

    assert (error.value.status, error.value.code, error.value.errors) == (413, None, [])


@pytest.mark.parametrize(
    'response',
    [
        httpx.Response(200, text='<html>login</html>'),
        httpx.Response(200, json={'source': {'uri': 'x', 'group': 'hologram'}, 'options': {}, 'checks': {}}),
        httpx.Response(204),
    ],
)
def test_unexpected_success_is_invalid(response: httpx.Response):
    with pytest.raises(InvalidResponse, match='/v1/inspect'):
        _papyrus(Server(response)).inspect(URL)


@pytest.mark.parametrize('retry_after', ['Wed, 21 Oct 2026 07:28:00 GMT', '-1', ''])
def test_odd_retry_after_is_ignored(retry_after: str):
    response = problem(503, 'not_ready', **{'retry-after': retry_after})

    with pytest.raises(APIError) as error:
        _papyrus(Server(response)).inspect(URL)

    assert error.value.retry_after is None


# - - - retries


@pytest.mark.parametrize(
    'failure',
    [
        problem(503, 'queue_full'),
        httpx.Response(502, text='bad gateway'),
        httpx.Response(503, text='unavailable'),
        httpx.ConnectError('refused'),
        httpx.ConnectTimeout('slow'),
    ],
)
def test_untouched_requests_are_retried(failure: httpx.Response | Exception, sleeps: list[float]):
    server = Server(failure, failure, OK)

    _papyrus(server).inspect(b'content', filename='a.txt')

    assert len(server.requests) == 3
    assert all(b'content' in request.content for request in server.requests)
    assert 0.5 <= sleeps[0] <= 0.75 and 1 <= sleeps[1] <= 1.5


@pytest.mark.parametrize(
    ('failure', 'kind'),
    [
        (problem(500, 'processing_failed'), APIError),
        (problem(502, 'source_fetch_failed'), APIError),
        (problem(502, 'destination_failed'), APIError),
        (problem(503, 'not_ready'), APIError),
        (problem(504, 'deadline_exceeded'), APIError),
        (httpx.Response(504, text='gateway timeout'), APIError),
        (httpx.Response(500, text='oops'), APIError),
        (httpx.ReadTimeout('slow'), TimedOut),
        (httpx.RemoteProtocolError('disconnected'), ConnectionFailed),
        (httpx.ReadError('reset'), ConnectionFailed),
    ],
)
def test_work_is_never_repeated(failure: httpx.Response | Exception, kind: type[Exception], sleeps: list[float]):
    server = Server(failure, OK)

    with pytest.raises(kind) as error:
        _papyrus(server).inspect(URL)

    assert len(server.requests) == 1
    assert sleeps == []
    if isinstance(failure, Exception):
        assert error.value.__cause__ is failure


def test_retries_give_up(sleeps: list[float]):
    server = Server(problem(503, 'queue_full'))

    with pytest.raises(APIError) as error:
        _papyrus(server, max_retries=1).inspect(URL)

    assert error.value.code == 'queue_full'
    assert len(server.requests) == 2 and len(sleeps) == 1


def test_retries_can_be_disabled(sleeps: list[float]):
    server = Server(httpx.ConnectError('refused'))

    with pytest.raises(ConnectionFailed):
        _papyrus(server, max_retries=0).inspect(URL)

    assert len(server.requests) == 1 and sleeps == []


@pytest.mark.parametrize(('asked', 'low', 'high'), [('4', 4, 6), ('3600', 30, 30)])
def test_retry_after_is_honored_up_to_a_cap(asked: str, low: float, high: float, sleeps: list[float]):
    _papyrus(Server(problem(503, 'queue_full', **{'retry-after': asked}), OK)).inspect(URL)

    assert low <= sleeps[0] <= high


def test_files_are_sent_again_from_the_beginning(tmp_path: Path, sleeps: list[float]):
    path = tmp_path / 'a.txt'
    path.write_bytes(b'content')
    server = Server(problem(503, 'queue_full'), OK, problem(503, 'queue_full'), OK)

    _papyrus(server).inspect(path)
    with path.open('rb') as file:
        _papyrus(server).inspect(file)

    assert len(server.requests) == 4
    assert all(b'content' in request.content and b'filename="a.txt"' in request.content for request in server.requests)


# - - - probes


@pytest.mark.parametrize('failure', [httpx.ConnectError('refused'), httpx.Response(502, text='bad gateway')])
def test_probes_raise_without_retrying(failure: httpx.Response | Exception, sleeps: list[float]):
    server = Server(failure)

    for probe in (_papyrus(server).health, _papyrus(server).ready):
        with pytest.raises((ConnectionFailed, APIError)):
            probe()

    assert [request.url.path for request in server.requests] == ['/health', '/ready']
    assert sleeps == []
