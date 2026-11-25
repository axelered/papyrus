from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.errors import UnsupportedFormat
from server.services.convert import ConvertService
from server.types.convert import ConvertFormat
from test.fixtures import samples
from test.fixtures.documents import HTML

OOXML = {ConvertFormat.DOCX: samples.docx, ConvertFormat.PPTX: samples.pptx, ConvertFormat.XLSX: samples.xlsx}


class FakeConvert:
    """Stands in for LibreOffice on the host: writes an OOXML sample of the requested format"""

    # the real method, over the fake convert
    modernize = ConvertService.modernize

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[str, ConvertFormat]] = []
        self.error = error

    async def convert(self, source: Path, target: Path, format: ConvertFormat) -> None:
        self.calls.append((source.suffix, format))
        if self.error is not None:
            raise self.error
        target.write_bytes(OOXML[format]())

    def close(self) -> None:
        pass


def _install(client: TestClient, fake: FakeConvert) -> FakeConvert:
    client.app.state.services.convert = fake
    return fake


def _leftovers(work_dir: Path) -> list[Path]:
    return list(work_dir.iterdir()) if work_dir.exists() else []


@pytest.mark.parametrize(
    ('name', 'content', 'label'),
    [('report.odt', samples.odt(), 'odt'), ('report.rtf', samples.RTF, 'rtf')],
)
def test_routes_documents_through_docx(
    client: TestClient, auth_headers: dict[str, str], work_dir: Path, name: str, content: bytes, label: str
):
    fake = _install(client, FakeConvert())

    response = client.post('/v1/markdown', headers=auth_headers, files={'file': (name, content)})

    assert response.status_code == 200
    assert response.json()['result']['content'].startswith('# Report')
    assert response.json()['source']['label'] == label
    assert fake.calls == [(f'.{label}', ConvertFormat.DOCX)]
    assert _leftovers(work_dir) == []


def test_skips_conversion_for_readable_formats(client: TestClient, auth_headers: dict[str, str]):
    fake = _install(client, FakeConvert())

    response = client.post('/v1/markdown', headers=auth_headers, files={'file': ('report.html', HTML)})

    assert response.status_code == 200
    assert fake.calls == []


def test_conversion_error_is_returned_and_workspace_removed(
    client: TestClient, auth_headers: dict[str, str], work_dir: Path
):
    _install(client, FakeConvert(UnsupportedFormat('file could not be opened')))

    response = client.post('/v1/markdown', headers=auth_headers, files={'file': ('report.odt', samples.odt())})

    assert response.status_code == 415
    assert response.json()['code'] == 'unsupported_format'
    assert _leftovers(work_dir) == []


@pytest.mark.full
@pytest.mark.parametrize(
    ('key', 'text'),
    [('doc', 'Revenue grew.'), ('ppt', 'Roadmap'), ('odp', 'Roadmap'), ('ods', 'region'), ('xls', 'region')],
)
def test_extracts_markdown_from_legacy_files(
    client: TestClient,
    auth_headers: dict[str, str],
    legacy: dict[str, Path],
    key: str,
    text: str,
):
    response = client.post(
        '/v1/markdown', headers=auth_headers, files={'file': (legacy[key].name, legacy[key].read_bytes())}
    )

    assert response.status_code == 200
    assert response.json()['source']['label'] == key
    assert text in response.json()['result']['content']
