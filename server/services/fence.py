import re


def fence(text: str, language: str) -> str:
    """One CommonMark fenced block; the fence is longer than any backtick run inside so content cannot break out"""
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    marker = '`' * max(3, longest + 1)
    body = text if not text or text.endswith('\n') else f'{text}\n'
    return f'{marker}{language}\n{body}{marker}\n'
