import re
from collections.abc import Iterable
from pathlib import Path

import httpx2
from mistralai.client import Mistral
from mistralai.client.errors import MistralError, NoResponseError, ResponseValidationError

from server.ocr.base import OcrAdapter, OcrTimeout, OcrTransient, data_url, status_error
from server.ocr.clean import clean_page

_IMAGE = re.compile(r'!\[[^\]]*\]\(([^)\s]+)\)')


class MistralAdapter(OcrAdapter):
    """Mistral OCR; the SDK does not retry unless given a retry config"""

    # the API gives no truncation signal: a very dense page comes back cut or empty with 200 (adr/ocr.md)

    def __init__(self, model: str, api_key: str, url: str | None = None, http_client: httpx2.AsyncClient | None = None):
        self._model = model
        self._client = Mistral(api_key=api_key, server_url=url, async_client=http_client)

    async def read(self, image: Path) -> str:
        try:
            response = await self._client.ocr.process_async(
                model=self._model,
                document={'type': 'image_url', 'image_url': await data_url(image)},
                include_image_base64=False,
                include_blocks=False,
            )
        except ResponseValidationError:
            raise OcrTransient('the OCR backend answer cannot be read') from None
        except MistralError as e:
            raise status_error(e.status_code, e.headers) from None
        except httpx2.TimeoutException:
            raise OcrTimeout('the OCR backend timed out') from None
        except (httpx2.HTTPError, NoResponseError) as e:
            raise OcrTransient(f'the OCR backend failed ({type(e).__name__})') from None
        if not response.pages:
            raise OcrTransient('the OCR backend answer has no pages')
        page = response.pages[0]
        return clean_page(_without_images(page.markdown, (image.id for image in page.images)))

    async def aclose(self) -> None:
        await self._client.__aexit__(None, None, None)


def _without_images(markdown: str, ids: Iterable[str]) -> str:
    """Drops references to the images of the response, they are not delivered; links of the document stay"""
    known = set(ids)
    return _IMAGE.sub(lambda match: '' if match.group(1) in known else match.group(0), markdown)
