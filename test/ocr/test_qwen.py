import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from server.ocr.base import OcrRateLimited, OcrRejected, OcrTimeout, OcrTransient, OcrTruncated
from server.ocr.qwen import MAX_TOKENS, PROMPT, QwenAdapter
from test.fixtures.ocr import Handler, mock_client

pytestmark = pytest.mark.anyio

URL = 'http://vllm.internal:8000/v1'


def _adapter(handler: Handler, **options: Any) -> QwenAdapter:
    return QwenAdapter('qwen-vl', URL, http_client=mock_client(handler), **options)


def _answer(content: str | None, finish_reason: str = 'stop') -> httpx2.Response:
    choice = {'index': 0, 'finish_reason': finish_reason, 'message': {'role': 'assistant', 'content': content}}
    body = {'id': 'x', 'object': 'chat.completion', 'created': 0, 'model': 'qwen-vl', 'choices': [choice]}
    return httpx2.Response(200, json=body)


async def test_sends_the_image_and_prompt(page_image: Path):
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _answer('# Title')

    extra = {'chat_template_kwargs': {'enable_thinking': False}}
    assert await _adapter(handler, api_key='secret', extra_body=extra).read(page_image) == '# Title'

    body = json.loads(requests[0].content)
    assert str(requests[0].url) == f'{URL}/chat/completions'
    assert requests[0].headers['authorization'] == 'Bearer secret'
    assert (body['model'], body['temperature'], body['max_tokens']) == ('qwen-vl', 0, MAX_TOKENS)
    assert body['chat_template_kwargs'] == {'enable_thinking': False}
    image, text = body['messages'][0]['content']
    assert image['image_url']['url'].startswith('data:image/jpeg;base64,/9j/')
    assert text['text'] == PROMPT


async def test_never_sends_the_environment_key(page_image: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'foreign')
    keys: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        keys.append(request.headers['authorization'])
        return _answer('')

    await _adapter(handler).read(page_image)

    assert keys == ['Bearer none']


@pytest.mark.parametrize(
    ('content', 'expected'),
    [
        ('', ''),
        ('  \n# Title\ntext\n ', '# Title\ntext'),
        ('```markdown\n# Title\n```', '# Title'),
        ('```md\nintro\n```py\nx = 1\n```\nend\n```', 'intro\n```py\nx = 1\n```\nend'),
        ('```python\nx = 1\n```', '```python\nx = 1\n```'),
        ('```\na\n```\ntext\n```\nb\n```', '```\na\n```\ntext\n```\nb\n```'),
        ('<think>\nlooks like a table\n</think>\n\ntext', 'text'),
        ('text about <think> and </think> tags', 'text about <think> and </think> tags'),
        ('<!-- a real comment -->\ntext', '<!-- a real comment -->\ntext'),
    ],
)
async def test_cleans_the_answer(page_image: Path, content: str, expected: str):
    assert await _adapter(lambda _: _answer(content)).read(page_image) == expected


@pytest.mark.parametrize(
    ('response', 'error'),
    [
        (_answer('| a | b', finish_reason='length'), OcrTruncated),
        (_answer(None), OcrTransient),
        (_answer('', finish_reason='content_filter'), OcrRejected),
        (httpx2.Response(200, json={'error': {'message': 'upstream down', 'code': 502}}), OcrTransient),
        (httpx2.Response(200, text='<html>gateway</html>'), OcrTransient),
        (httpx2.Response(401, json={'error': {'message': 'bad key'}}), OcrRejected),
        (httpx2.Response(400, json={'error': {'message': 'bad image'}}), OcrRejected),
        (httpx2.Response(503, text='busy'), OcrTransient),
    ],
)
async def test_maps_failures_without_sdk_retries(page_image: Path, response: httpx2.Response, error: type[Exception]):
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return response

    with pytest.raises(error) as raised:
        await _adapter(handler).read(page_image)

    assert calls == 1
    assert raised.value.__cause__ is None
    assert not any(text in str(raised.value) for text in ('bad', 'busy', 'upstream', 'vllm'))


@pytest.mark.parametrize(
    ('headers', 'delay'),
    [({'retry-after': '7'}, 7.0), ({}, None), ({'retry-after': 'Wed, 21 Oct 2026 07:28:00 GMT'}, None)],
)
async def test_rate_limit_carries_the_delay(page_image: Path, headers: dict[str, str], delay: float | None):
    with pytest.raises(OcrRateLimited) as raised:
        await _adapter(lambda _: httpx2.Response(429, headers=headers)).read(page_image)

    assert raised.value.retry_after == delay


@pytest.mark.parametrize(
    ('failure', 'error'), [(httpx2.ConnectError('refused'), OcrTransient), (httpx2.ReadTimeout('slow'), OcrTimeout)]
)
async def test_maps_network_failures(page_image: Path, failure: Exception, error: type[Exception]):
    def handler(_: httpx2.Request) -> httpx2.Response:
        raise failure

    with pytest.raises(error):
        await _adapter(handler).read(page_image)


async def test_closes_its_client():
    client = mock_client(lambda _: _answer(''))

    await QwenAdapter('qwen-vl', URL, http_client=client).aclose()

    assert client.is_closed
