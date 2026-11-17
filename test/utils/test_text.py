import pytest

from server.utils.text import decode_text, fence


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('x = 1\n', '```python\nx = 1\n```\n'),
        ('x = 1', '```python\nx = 1\n```\n'),
        ('', '```python\n```\n'),
        ('s = """\n```\n"""\n', '````python\ns = """\n```\n"""\n````\n'),
        ('a = "`````"\n', '``````python\na = "`````"\n``````\n'),
    ],
)
def test_fence_cannot_be_broken_out_of(text: str, expected: str):
    assert fence(text, 'python') == expected


@pytest.mark.parametrize(
    ('raw', 'text'),
    [
        (b'', ''),
        ('x = "café"\n'.encode(), 'x = "café"\n'),
        ('﻿x = 1\n'.encode(), 'x = 1\n'),
        ('x = "日本語のテキスト"\n'.encode('utf-16'), 'x = "日本語のテキスト"\n'),
    ],
)
def test_decode_text_detects_charset(raw: bytes, text: str):
    assert decode_text(raw) == text
