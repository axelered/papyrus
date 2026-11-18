import asyncio
import json
import os
import shutil
from pathlib import Path

from server.errors import PageOutOfRange, ProcessingFailed, UnsupportedFormat
from server.services import lok
from server.services.base import Service, pool_errors
from server.types.convert import ConvertFormat
from server.utils.pool import ProcessPool

TIMEOUT = 120
MAX_TASKS = 50

# silent dialogs: password prompts fail the load instead of hanging the worker
LOAD_OPTIONS = 'Batch=true'
# read by lok_init: remote hosts must fully match the reserved `invalid` name, file:// external references are blocked
ENVIRONMENT = {'LOK_HOST_ALLOWLIST': 'invalid', 'LOK_ALLOWED_EXTREF_PATHS': ''}
# no lock files next to sources: a worker killed mid-load would leave one that makes the file unopenable
REGISTRY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
    '<item oor:path="/org.openoffice.Office.Common/Misc">'
    '<prop oor:name="UseDocumentOOoLockFile" oor:op="fuse"><value>false</value></prop></item>'
    '<item oor:path="/org.openoffice.Office.Common/Misc">'
    '<prop oor:name="UseDocumentSystemFileLocking" oor:op="fuse"><value>false</value></prop></item>'
    '</oor:items>'
)

_office: lok.Office | None = None


def convert_document(
    source: str, target: str, format: str, profiles: str, save_options: str = '', page: int | None = None
) -> None:
    """Pool worker: LibreOffice is started on the first task and stays loaded until pebble recycles the worker

    `page` exports that single page of a PDF target.
    """
    if page is not None:
        save_options = page_range(page)
    office = _ensure_office(Path(profiles))
    try:
        office.convert(Path(source), Path(target), format, LOAD_OPTIONS, save_options)
    except lok.LoadError:
        raise UnsupportedFormat('file could not be opened, it may be corrupted, encrypted or mislabeled') from None
    except lok.SaveError:
        # a page range past the end fails the save without telling the page count; every document exports page 1
        if page is not None and page > 1:
            raise PageOutOfRange(f'page {page} is out of range') from None
        raise UnsupportedFormat(f'document cannot be converted to {format}') from None
    except lok.LokError as e:
        raise ProcessingFailed(f'conversion failed ({e})') from None
    if not Path(target).is_file():
        raise ProcessingFailed('conversion produced no output')


def page_range(page: int) -> str:
    """PDF export filter option, in the JSON form LibreOffice accepts as save options"""
    return json.dumps({'PageRange': {'type': 'string', 'value': str(page)}})


def _ensure_office(profiles: Path) -> lok.Office:
    global _office
    if _office is None:
        profile = _worker_profile(profiles)
        # dconf and fontconfig want a writable cache, the runtime user has no home
        os.environ.update(ENVIRONMENT, XDG_CACHE_HOME=str(profile / 'cache'))
        try:
            _office = lok.Office(profile)
        except lok.LokError as e:
            raise ProcessingFailed(f'converter is unavailable ({e})') from None
    return _office


def _worker_profile(profiles: Path) -> Path:
    """Profiles are locked by LibreOffice so each worker owns one; dirs of recycled workers are removed here"""
    profiles.mkdir(parents=True, exist_ok=True)
    for path in profiles.iterdir():
        if path.name.isdigit() and not _alive(int(path.name)):
            shutil.rmtree(path, ignore_errors=True)
    profile = profiles / str(os.getpid())
    (profile / 'user').mkdir(parents=True, exist_ok=True)
    (profile / 'user' / 'registrymodifications.xcu').write_text(REGISTRY, encoding='utf-8')
    return profile


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ConvertService(Service):
    def __init__(self, processes: int, profiles: Path, timeout: float = TIMEOUT, max_tasks: int = MAX_TASKS):
        self._profiles = profiles
        self._pool = ProcessPool('convert', processes, timeout=timeout, max_tasks=max_tasks)

    async def convert(self, source: Path, target: Path, format: ConvertFormat, page: int | None = None) -> None:
        """`page` exports only that page and is valid for PDF targets only"""
        if page is not None and format != ConvertFormat.PDF:
            raise ValueError('page requires a pdf target')
        with pool_errors():
            await self._pool.run(
                convert_document, str(source), str(target), format.value, str(self._profiles), '', page
            )

    async def aclose(self) -> None:
        await self._pool.aclose()
        await asyncio.to_thread(shutil.rmtree, self._profiles, ignore_errors=True)
