"""Documents and the mock transport shared by the tests; nothing here imports the server"""

import random

import httpx

HTML = b'<html><head><title>Quarterly</title></head><body><h1>Report</h1><p>Revenue grew.</p></body></html>'
CSV = b'name,amount\nalpha,1\nbeta,2\n'
RTF = b'{\\rtf1\\ansi Hello from rtf}'
# seeded random bytes that no converter accepts
BINARY = random.Random(0).randbytes(512)

JPEG, PNG, WEBP = b'\xff\xd8\xff', b'\x89PNG', b'RIFF'


def pdf(*sizes: tuple[int, int]) -> bytes:
    """Blank PDF with one page per (width, height) in points"""
    kids = ' '.join(f'{i + 3} 0 R' for i in range(len(sizes)))
    objects = [
        '<< /Type /Catalog /Pages 2 0 R >>',
        f'<< /Type /Pages /Kids [{kids}] /Count {len(sizes)} >>',
        *(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] >>' for w, h in sizes),
    ]
    body, offsets = '%PDF-1.4\n', []
    for i, item in enumerate(objects):
        offsets.append(f'{len(body):010d} 00000 n \n')
        body += f'{i + 1} 0 obj\n{item}\nendobj\n'
    xref = f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n{"".join(offsets)}'
    trailer = f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{len(body)}\n%%EOF\n'
    return (body + xref + trailer).encode()


class Server:
    """Mock transport handler: replays the outcomes in order, the last one forever, and records the requests"""

    def __init__(self, *outcomes: httpx.Response | Exception):
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.outcomes[min(len(self.requests), len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def problem(status: int, code: str, **headers: str) -> httpx.Response:
    body = {'title': 'Error', 'status': status, 'code': code, 'detail': 'why'}
    return httpx.Response(status, json=body, headers={**headers, 'content-type': 'application/problem+json'})
