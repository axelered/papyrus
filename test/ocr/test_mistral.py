import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from server.ocr.base import OcrRejected, OcrThrottled, OcrTimeout, OcrTransient
from server.ocr.mistral import MistralAdapter
from test.fixtures.ocr import Handler, mock_client

pytestmark = pytest.mark.anyio


def _adapter(handler: Handler, **options: Any) -> MistralAdapter:
    return MistralAdapter('mistral-ocr-latest', 'secret', http_client=mock_client(handler), **options)


def _answer(markdown: str, *images: str) -> httpx2.Response:
    corners = dict.fromkeys(('top_left_x', 'top_left_y', 'bottom_right_x', 'bottom_right_y'), 1)
    page = {
        'index': 0,
        'markdown': markdown,
        'images': [{'id': id, **corners} for id in images],
        'dimensions': {'dpi': 200, 'height': 2339, 'width': 1654},
    }
    return _pages(page)


def _pages(*pages: dict[str, Any]) -> httpx2.Response:
    body = {'pages': pages, 'model': 'mistral-ocr-latest', 'usage_info': {'pages_processed': len(pages)}}
    return httpx2.Response(200, json=body)


@pytest.mark.parametrize(
    ('options', 'url'),
    [
        ({}, 'https://api.mistral.ai/v1/ocr'),
        ({'url': 'https://ocr.axelered.com'}, 'https://ocr.axelered.com/v1/ocr'),
    ],
)
async def test_sends_the_image(page_image: Path, options: dict[str, str], url: str):
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _answer('# Title')

    assert await _adapter(handler, **options).read(page_image) == '# Title'

    body = json.loads(requests[0].content)
    assert str(requests[0].url) == url
    assert requests[0].headers['authorization'] == 'Bearer secret'
    assert body['model'] == 'mistral-ocr-latest'
    assert body['document']['type'] == 'image_url'
    assert body['document']['image_url'].startswith('data:image/jpeg;base64,/9j/')
    assert (body['include_image_base64'], body['include_blocks']) == (False, False)


async def test_drops_only_references_to_extracted_images(page_image: Path):
    markdown = '![img-0.jpeg](img-0.jpeg)\n\ntext ![logo](https://axelered.com/logo.png)'

    read = await _adapter(lambda _: _answer(markdown, 'img-0.jpeg')).read(page_image)

    assert read == '\n\ntext ![logo](https://axelered.com/logo.png)'  # the service trims


async def test_blank_page_is_empty(page_image: Path):
    assert await _adapter(lambda _: _answer('')).read(page_image) == ''


@pytest.mark.parametrize(
    ('response', 'error'),
    [
        (_pages(), OcrTransient),
        (httpx2.Response(200, json={'unexpected': True}), OcrTransient),
        (httpx2.Response(401, json={'message': 'bad key'}), OcrRejected),
        (httpx2.Response(422, json={'detail': []}), OcrRejected),
        (httpx2.Response(500, text='busy'), OcrTransient),
        (httpx2.Response(429, headers={'retry-after': '3'}), OcrThrottled),
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
    assert 'bad' not in str(raised.value) and 'busy' not in str(raised.value)
    assert getattr(raised.value, 'retry_after', 3.0) == 3.0


@pytest.mark.parametrize(
    ('failure', 'error'), [(httpx2.ConnectError('refused'), OcrTransient), (httpx2.ReadTimeout('slow'), OcrTimeout)]
)
async def test_maps_network_failures(page_image: Path, failure: Exception, error: type[Exception]):
    def handler(_: httpx2.Request) -> httpx2.Response:
        raise failure

    with pytest.raises(error):
        await _adapter(handler).read(page_image)
