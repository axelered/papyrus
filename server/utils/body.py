import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Request
from starlette.datastructures import UploadFile

from server.utils.problem import PayloadTooLarge, UnsupportedMediaType, ValidationFailed

JSON = 'application/json'
MULTIPART = 'multipart/form-data'

MAX_JSON_BODY = 1024 * 1024
_CHUNK = 1024 * 1024


@dataclass
class Body:
    """A JSON object read either from the body or from a multipart field, plus the uploaded files"""

    content_type: str
    data: dict[str, Any]
    files: dict[str, Path] = field(default_factory=dict)


async def read_body(
    request: Request,
    *,
    new_file: Callable[[str], Path],
    data_field: str,
    max_file_size: int,
    max_files: int,
    max_json: int = MAX_JSON_BODY,
) -> Body:
    """JSON object body, or multipart with the object in `data_field` and file parts written via `new_file`"""
    content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if content_type == JSON:
        raw = await request.body()
        if len(raw) > max_json:
            raise PayloadTooLarge(f'JSON body exceeds {max_json} bytes')
        return Body(content_type, decode_object(raw, 'body'))
    if content_type == MULTIPART:
        return await _read_multipart(request, new_file, data_field, max_file_size, max_files)
    raise UnsupportedMediaType(f'expected {JSON} or {MULTIPART}')


async def _read_multipart(
    request: Request, new_file: Callable[[str], Path], data_field: str, max_file_size: int, max_files: int
) -> Body:
    files: dict[str, Path] = {}
    raw: str | None = None
    async with request.form(max_files=max_files) as form:
        for name, value in form.multi_items():
            if isinstance(value, UploadFile):
                if name in files:
                    raise ValidationFailed(f'duplicate file part {name!r}')
                files[name] = await save_upload(value, new_file(value.filename or name), max_file_size)
            elif name == data_field and raw is None:
                raw = value
            else:
                raise ValidationFailed(f'unexpected form field {name!r}, options belong in {data_field!r}')
    return Body(MULTIPART, decode_object(raw, data_field) if raw else {}, files)


async def save_upload(upload: UploadFile, path: Path, max_size: int) -> Path:
    written = 0
    with path.open('wb') as file:
        while chunk := await upload.read(_CHUNK):
            written += len(chunk)
            if written > max_size:
                raise PayloadTooLarge(f'upload exceeds {max_size} bytes')
            file.write(chunk)
    return path


def decode_object(raw: str | bytes, name: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ValidationFailed(f'{name} is not valid JSON') from e
    if not isinstance(data, dict):
        raise ValidationFailed(f'{name} must be a JSON object')
    return data
