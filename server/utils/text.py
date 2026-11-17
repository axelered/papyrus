import re


def fence(text: str, language: str) -> str:
    """One CommonMark fenced block; the fence is longer than any backtick run inside so content cannot break out"""
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    marker = '`' * max(3, longest + 1)
    body = text if not text or text.endswith('\n') else f'{text}\n'
    return f'{marker}{language}\n{body}{marker}\n'


def decode_text(raw: bytes) -> str:
    """UTF-8 (BOM tolerated) first, charset detection only as a fallback"""
    if not raw:
        return ''
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        pass
    # detection only for non utf-8 input, short single byte texts are ambiguous between code pages
    from charset_normalizer import from_bytes

    best = from_bytes(raw).best()
    return str(best) if best is not None else raw.decode('utf-8', errors='replace')
