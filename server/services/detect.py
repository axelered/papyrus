import asyncio
import logging
import threading
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from magika import Magika
from magika.types import ContentTypeInfo

from server.errors import ProcessingFailed
from server.services.base import Service
from server.types.common import FormatGroup, FormatLabel

logger = logging.getLogger('papyrus.detect')


@dataclass(frozen=True)
class Format:
    label: str
    mime_type: str
    extension: str
    group: FormatGroup
    base: FormatLabel | None = None
    text: bool = False
    derived: bool = False
    aliases: tuple[str, ...] = field(default=(), compare=False)

    @property
    def extensions(self) -> tuple[str, ...]:
        return (self.extension, *self.aliases)


_G = FormatGroup
_L = FormatLabel


class Formats:
    PDF = Format(_L.PDF, 'application/pdf', 'pdf', _G.DOCUMENT)
    DOCX = Format(
        _L.DOCX,
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'docx',
        _G.DOCUMENT,
        _L.ZIP,
        aliases=('docm',),
    )
    PPTX = Format(
        _L.PPTX,
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'pptx',
        _G.PRESENTATION,
        _L.ZIP,
        aliases=('pptm',),
    )
    XLSX = Format(
        _L.XLSX,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'xlsx',
        _G.SPREADSHEET,
        _L.ZIP,
        aliases=('xlsm',),
    )
    EPUB = Format(_L.EPUB, 'application/epub+zip', 'epub', _G.DOCUMENT, _L.ZIP)
    ZIP = Format(_L.ZIP, 'application/zip', 'zip', _G.ARCHIVE)
    DOC = Format(_L.DOC, 'application/msword', 'doc', _G.DOCUMENT, aliases=('dot',))
    PPT = Format(_L.PPT, 'application/vnd.ms-powerpoint', 'ppt', _G.PRESENTATION)
    XLS = Format(_L.XLS, 'application/vnd.ms-excel', 'xls', _G.SPREADSHEET)
    OUTLOOK = Format(_L.OUTLOOK, 'application/vnd.ms-outlook', 'msg', _G.EMAIL)
    RTF = Format(_L.RTF, 'text/rtf', 'rtf', _G.DOCUMENT, _L.TXT, text=True)
    ODT = Format(_L.ODT, 'application/vnd.oasis.opendocument.text', 'odt', _G.DOCUMENT, _L.ZIP)
    ODS = Format(_L.ODS, 'application/vnd.oasis.opendocument.spreadsheet', 'ods', _G.SPREADSHEET, _L.ZIP)
    ODP = Format(_L.ODP, 'application/vnd.oasis.opendocument.presentation', 'odp', _G.PRESENTATION, _L.ZIP)
    TXT = Format(_L.TXT, 'text/plain', 'txt', _G.TEXT, text=True, aliases=('text',))
    MARKDOWN = Format(_L.MARKDOWN, 'text/markdown', 'md', _G.MARKUP, _L.TXT, text=True, aliases=('markdown',))
    CSV = Format(_L.CSV, 'text/csv', 'csv', _G.DATA, _L.TXT, text=True)
    TSV = Format(_L.TSV, 'text/tab-separated-values', 'tsv', _G.DATA, _L.TXT, text=True, aliases=('tab',))
    HTML = Format(_L.HTML, 'text/html', 'html', _G.MARKUP, _L.TXT, text=True, aliases=('htm', 'xhtml'))
    XML = Format(_L.XML, 'application/xml', 'xml', _G.MARKUP, _L.TXT, text=True)
    RSS = Format(_L.RSS, 'application/rss+xml', 'rss', _G.MARKUP, _L.XML, text=True)
    ATOM = Format(_L.ATOM, 'application/atom+xml', 'atom', _G.MARKUP, _L.XML, text=True)
    JSON = Format(_L.JSON, 'application/json', 'json', _G.DATA, _L.TXT, text=True)
    JSONL = Format(_L.JSONL, 'application/jsonl', 'jsonl', _G.DATA, _L.JSON, text=True, aliases=('ndjson',))
    IPYNB = Format(_L.IPYNB, 'application/x-ipynb+json', 'ipynb', _G.CODE, _L.JSON, text=True)
    PNG = Format(_L.PNG, 'image/png', 'png', _G.IMAGE)
    JPEG = Format(_L.JPEG, 'image/jpeg', 'jpg', _G.IMAGE, aliases=('jpeg', 'jpe'))
    WEBP = Format(_L.WEBP, 'image/webp', 'webp', _G.IMAGE)
    GIF = Format(_L.GIF, 'image/gif', 'gif', _G.IMAGE)
    BMP = Format(_L.BMP, 'image/bmp', 'bmp', _G.IMAGE, aliases=('dib',))
    TIFF = Format(_L.TIFF, 'image/tiff', 'tiff', _G.IMAGE, aliases=('tif',))
    AVIF = Format(_L.AVIF, 'image/avif', 'avif', _G.IMAGE)
    UNKNOWN = Format(_L.UNKNOWN, 'application/octet-stream', 'bin', _G.UNKNOWN)


# magika labels that say nothing about the format, so the extension decides
_NO_SIGNAL = frozenset({'empty', 'undefined'})
# magika labels every ISO BMFF file mp4; the ftyp major brand tells AVIF apart
_AVIF_BRANDS = frozenset({b'avif', b'avis'})

EXPLICIT: dict[str, Format] = {value.label: value for value in vars(Formats).values() if isinstance(value, Format)}


@cache
def _magika_kb() -> dict[str, ContentTypeInfo]:
    # todo: private magika API (reads the bundled JSON, no model load); pinned by tests, revisit on magika upgrades
    kb = Magika._load_content_types_kb(files('magika') / 'config' / 'content_types_kb.min.json')
    return {str(label): info for label, info in kb.items()}


def _derive(info: ContentTypeInfo) -> Format:
    """Formats outside the explicit table built from magika metadata; only text ones are processable"""
    extension = info.extensions[0] if info.extensions else ('txt' if info.is_text else 'bin')
    if info.is_text:
        group = FormatGroup.CODE if info.group == 'code' else FormatGroup.TEXT
        return Format(
            str(info.label),
            info.mime_type,
            extension,
            group,
            FormatLabel.TXT,
            text=True,
            derived=True,
            aliases=tuple(info.extensions[1:]),
        )
    return Format(str(info.label), info.mime_type, extension, FormatGroup.UNKNOWN, derived=True)


def lookup(label: str) -> Format:
    if label in EXPLICIT:
        return EXPLICIT[label]
    info = _magika_kb().get(label)
    return _derive(info) if info is not None else Formats.UNKNOWN


@cache
def _extension_index() -> dict[str, Format]:
    index: dict[str, Format] = {}
    for info in _magika_kb().values():
        if info.is_text and str(info.label) not in EXPLICIT:
            derived = _derive(info)
            for extension in derived.extensions:
                index.setdefault(extension, derived)
    for explicit in EXPLICIT.values():
        if explicit.label != FormatLabel.UNKNOWN:
            index.update(dict.fromkeys(explicit.extensions, explicit))
    return index


def from_extension(extension: str) -> Format | None:
    return _extension_index().get(extension.lower().lstrip('.'))


def _ancestors(value: Format) -> set[str]:
    labels: set[str] = set()
    while value.base is not None and value.base not in labels:
        labels.add(value.base)
        value = EXPLICIT[value.base]
    return labels


def resolve(detected: str, extension: str) -> Format:
    """Content wins; the extension refines generic content labels and fills in for unknown content"""
    if detected in _NO_SIGNAL:
        detected = FormatLabel.UNKNOWN
    by_extension = from_extension(extension) if extension else None
    if by_extension is not None and detected in _ancestors(by_extension):
        return by_extension
    content = lookup(detected)
    if (
        by_extension is not None
        and by_extension.text
        and content.text
        and by_extension.label not in _ancestors(content)
    ):
        # magika separates text from binary reliably but confuses small text formats (html vs erb, js vs ts)
        return by_extension
    if detected == FormatLabel.UNKNOWN:
        return by_extension or Formats.UNKNOWN
    return content


def extension_match(value: Format, extension: str) -> bool | None:
    """Whether a name's extension declares `value` or a generic ancestor of it; None without an extension"""
    extension = extension.lower().lstrip('.')
    if not extension:
        return None
    declared = from_extension(extension)
    if declared is None and extension in Formats.UNKNOWN.extensions:
        declared = Formats.UNKNOWN
    return declared is not None and (declared.label == value.label or declared.label in _ancestors(value))


def _brand(path: Path) -> bytes:
    with path.open('rb') as file:
        head = file.read(12)
    return head[8:12] if head[4:8] == b'ftyp' else b''


@dataclass(frozen=True)
class Document:
    path: Path
    format: Format


def normalize_extension(path: Path, value: Format) -> Path:
    """Renames the file so every downstream tool sees the detected format's extension (stem is kept)"""
    if value.label == FormatLabel.UNKNOWN or path.suffix.lower().lstrip('.') in value.extensions:
        return path
    return path.rename(path.with_name(f'{path.stem or path.name}.{value.extension}'))


class DetectService(Service):
    """Inline service: magika runs in the API process, onnxruntime releases the GIL while predicting"""

    def __init__(self) -> None:
        self._magika: Any = None
        self._lock = threading.Lock()

    async def warm(self) -> None:
        await asyncio.to_thread(self._model)

    async def detect(self, path: Path) -> Document:
        try:
            label = await asyncio.to_thread(self._identify, path)
        except Exception as e:
            logger.warning('type detection failed for %s', path.name, exc_info=e)
            raise ProcessingFailed('type detection failed') from e
        value = resolve(label, path.suffix.lstrip('.'))
        logger.info('detected %s as %s (magika %s)', path.name, value.label, label)
        return Document(normalize_extension(path, value), value)

    def _identify(self, path: Path) -> str:
        result = self._model().identify_path(path)
        if not result.ok:
            raise OSError(f'magika status {result.status}')
        label = str(result.output.label)
        if label == 'mp4' and _brand(path) in _AVIF_BRANDS:
            return FormatLabel.AVIF
        return label

    def _model(self) -> Any:
        with self._lock:
            if self._magika is None:
                self._magika = Magika()
            return self._magika
