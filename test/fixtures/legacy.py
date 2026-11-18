import asyncio
from pathlib import Path

import pytest

from server.services.convert import convert_document
from server.utils.pool import ProcessPool
from test.fixtures import samples

PASSWORD_OPTIONS = ',Password=secretPASSWORDEND'


@pytest.fixture(scope='module')
def legacy(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Legacy and OpenDocument files produced by the engine itself from OOXML and ODT samples; needs LibreOffice"""
    return asyncio.run(_produce(tmp_path_factory.mktemp('legacy')))


async def _produce(root: Path) -> dict[str, Path]:
    pool = ProcessPool('fixtures', processes=1, timeout=120)
    jobs = {
        'doc': ('report.docx', samples.docx(), 'doc', ''),
        'ppt': ('deck.pptx', samples.pptx(), 'ppt', ''),
        'odp': ('deck.pptx', samples.pptx(), 'odp', ''),
        'xls': ('sheet.xlsx', samples.xlsx(), 'xls', ''),
        'ods': ('sheet.xlsx', samples.xlsx(), 'ods', ''),
        'encrypted': ('locked.odt', samples.odt(), 'odt', PASSWORD_OPTIONS),
    }
    paths = {}
    try:
        for key, (name, content, format, options) in jobs.items():
            source = root / name
            source.write_bytes(content)
            target = root / f'{key}.{format}'
            await pool.run(convert_document, str(source), str(target), format, str(root / 'profiles'), options)
            paths[key] = target
    finally:
        await pool.aclose()
    return paths
