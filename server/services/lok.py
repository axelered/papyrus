import ctypes
from pathlib import Path

INSTALL_DIR = Path('/usr/lib/libreoffice/program')
LIBRARY = 'libmergedlo.so'


class LokError(Exception):
    pass


class LoadError(LokError):
    pass


class SaveError(LokError):
    pass


class _Kit(ctypes.Structure):
    pass


class _Document(ctypes.Structure):
    pass


_KitP = ctypes.POINTER(_Kit)
_DocumentP = ctypes.POINTER(_Document)


# struct prefixes in header order: members are only ever appended, nSize tells how many the library has
class _KitClass(ctypes.Structure):
    _fields_ = (
        ('nSize', ctypes.c_size_t),
        ('destroy', ctypes.CFUNCTYPE(None, _KitP)),
        ('documentLoad', ctypes.CFUNCTYPE(_DocumentP, _KitP, ctypes.c_char_p)),
        ('getError', ctypes.CFUNCTYPE(ctypes.c_void_p, _KitP)),
        ('documentLoadWithOptions', ctypes.CFUNCTYPE(_DocumentP, _KitP, ctypes.c_char_p, ctypes.c_char_p)),
        ('freeError', ctypes.CFUNCTYPE(None, ctypes.c_void_p)),
    )


class _DocumentClass(ctypes.Structure):
    _fields_ = (
        ('nSize', ctypes.c_size_t),
        ('destroy', ctypes.CFUNCTYPE(None, _DocumentP)),
        ('saveAs', ctypes.CFUNCTYPE(ctypes.c_int, _DocumentP, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p)),
    )


_Kit._fields_ = (('pClass', ctypes.POINTER(_KitClass)),)
_Document._fields_ = (('pClass', ctypes.POINTER(_DocumentClass)),)


def available(install_dir: Path = INSTALL_DIR) -> bool:
    return (install_dir / LIBRARY).is_file()


class Office:
    """One LibreOffice per process: LOK cannot be initialized twice and is never destroyed (teardown hangs)

    Must be used from the main thread of the process that created it.
    """

    def __init__(self, profile: Path, install_dir: Path = INSTALL_DIR):
        try:
            library = ctypes.CDLL(str(install_dir / LIBRARY), mode=ctypes.RTLD_GLOBAL)
        except OSError as e:
            raise LokError(f'cannot load {LIBRARY}: {e}') from None
        hook = library.libreofficekit_hook_2
        hook.restype = _KitP
        hook.argtypes = (ctypes.c_char_p, ctypes.c_char_p)
        kit = hook(str(install_dir).encode(), profile.as_uri().encode())
        if not kit:
            raise LokError('LibreOfficeKit failed to initialize')
        self._kit = kit
        self._class = kit.contents.pClass.contents
        if self._class.nSize < ctypes.sizeof(_KitClass):
            raise LokError('LibreOfficeKit is too old')

    def convert(self, source: Path, target: Path, format: str, load_options: str = '', save_options: str = '') -> None:
        document = self._class.documentLoadWithOptions(self._kit, source.as_uri().encode(), load_options.encode())
        if not document:
            raise LoadError(self._error())
        methods = document.contents.pClass.contents
        try:
            if methods.nSize < ctypes.sizeof(_DocumentClass):
                raise LokError('LibreOfficeKit document class is too old')
            if not methods.saveAs(document, target.as_uri().encode(), format.encode(), save_options.encode()):
                raise SaveError(self._error())
        finally:
            methods.destroy(document)

    def _error(self) -> str:
        pointer = self._class.getError(self._kit)
        if not pointer:
            return 'unknown error'
        try:
            return ctypes.string_at(pointer).decode('utf-8', errors='replace')
        finally:
            self._class.freeError(pointer)
