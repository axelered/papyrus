import re

_MARKER = re.compile(r'<!--\s*(?:Page|Slide) number:\s*\d+\s*-->')


def clean_page(markdown: str) -> str:
    """Page markers belong to the caller, one written by the model would corrupt chunk pages"""
    return _MARKER.sub('', markdown).replace('\r\n', '\n').strip()
