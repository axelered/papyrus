"""Request and response shape against real backends, run by hand: `pytest -m live` (adr/development.md)"""

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from server.ocr.mistral import MistralAdapter
from server.ocr.qwen import QwenAdapter

pytestmark = [pytest.mark.anyio, pytest.mark.live]


@pytest.fixture
def invoice(tmp_path: Path) -> Path:
    page = Image.new('RGB', (900, 500), 'white')
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=32)
    draw.text((40, 30), 'Invoice 2026-117', font=font, fill='black')
    for row, cells in enumerate([('Item', 'Qty', 'Price'), ('Widget', '3', '42.50'), ('Gadget', '1', '17.00')]):
        for column, cell in enumerate(cells):
            box = (40 + column * 260, 120 + row * 70, 300 + column * 260, 190 + row * 70)
            draw.rectangle(box, outline='black', width=2)
            draw.text((box[0] + 15, box[1] + 15), cell, font=font, fill='black')
    path = tmp_path / 'invoice.jpg'
    page.save(path, quality=85)
    return path


def _env(name: str) -> str:
    return os.environ.get(name) or pytest.skip(f'{name} is not set')


def _check(markdown: str) -> None:
    rows = [[cell.strip() for cell in line.strip('| ').split('|')] for line in markdown.splitlines() if '|' in line]
    assert 'Invoice 2026-117' in markdown
    assert ['Widget', '3', '42.50'] in rows and ['Gadget', '1', '17.00'] in rows


async def test_qwen(invoice: Path):
    adapter = QwenAdapter(
        _env('PAPYRUS_LIVE_QWEN_MODEL'), _env('PAPYRUS_LIVE_QWEN_URL'), os.environ.get('PAPYRUS_LIVE_QWEN_KEY')
    )

    _check(await adapter.read(invoice))
    await adapter.aclose()


async def test_mistral(invoice: Path):
    adapter = MistralAdapter('mistral-ocr-latest', _env('PAPYRUS_LIVE_MISTRAL_KEY'))

    _check(await adapter.read(invoice))
    await adapter.aclose()
