import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import UploadFile

from server.config import Config
from server.errors import (
    DeadlineExceeded,
    SourceTooLarge,
    UnsupportedMediaType,
    UnsupportedUri,
    ValidationFailed,
)
from server.io.uri import PART_SCHEME
from server.io.workspace import Workspace

DEFAULT_PART = 'file'
METADATA_FIELD = 'metadata'

_MAX_JSON_BODY = 1024 * 1024
_MAX_FILE_PARTS = 8
_CHUNK = 1024 * 1024


class OutputMode(StrEnum):
    FILE = 'file'
    JSON = 'json'


class OpRequest(BaseModel):
    """Common request contract; operations subclass it, override `output` defaults and add typed `options`"""

    model_config = ConfigDict(extra='forbid')

    source: str = Field(description='file://<part>, http(s):// URL or <protocol>://<connector>/<path>')
    destination: str | None = Field(default=None, description='Connector URI to write the result to')
    output: OutputMode = OutputMode.JSON


@dataclass
class Op[M: OpRequest]:
    model: M
    parts: dict[str, Path]
    workspace: Workspace


@asynccontextmanager
async def op_context[M: OpRequest](request: Request, model: type[M]) -> AsyncIterator[Op[M]]:
    """Admission slot, workspace and deadline around one operation; parses and validates the request body"""
    config: Config = request.app.state.config
    with request.app.state.admission.slot(), Workspace(config.limits.tmp_dir) as workspace:
        deadline = asyncio.timeout(config.limits.deadline)
        try:
            async with deadline:
                op = await parse_op(request, model, workspace, int(config.limits.max_file_size))
                if op.model.destination:
                    request.app.state.transfer.check_destination(op.model.destination)
                yield op
        except TimeoutError as e:
            if deadline.expired():
                raise DeadlineExceeded(f'request exceeded {config.limits.deadline}s') from e
            raise


async def parse_op[M: OpRequest](request: Request, model: type[M], workspace: Workspace, max_size: int) -> Op[M]:
    content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if content_type == 'application/json':
        data, parts = await _read_json(request), {}
    elif content_type == 'multipart/form-data':
        data, parts = await _read_multipart(request, workspace, max_size)
    else:
        raise UnsupportedMediaType('expected application/json or multipart/form-data')

    try:
        parsed = model.model_validate(data)
    except ValidationError as e:
        raise RequestValidationError(e.errors()) from e
    if content_type == 'application/json' and parsed.source.startswith(f'{PART_SCHEME}://'):
        raise UnsupportedUri('file:// sources require multipart/form-data')
    return Op(model=parsed, parts=parts, workspace=workspace)


async def _read_json(request: Request) -> dict[str, Any]:
    body = await request.body()
    if len(body) > _MAX_JSON_BODY:
        raise ValidationFailed(f'JSON body exceeds {_MAX_JSON_BODY} bytes')
    return _decode_object(body, 'body')


async def _read_multipart(
    request: Request, workspace: Workspace, max_size: int
) -> tuple[dict[str, Any], dict[str, Path]]:
    parts: dict[str, Path] = {}
    metadata: str | None = None
    async with request.form(max_files=_MAX_FILE_PARTS) as form:
        for name, value in form.multi_items():
            if isinstance(value, UploadFile):
                if name in parts:
                    raise ValidationFailed(f'duplicate file part {name!r}')
                parts[name] = await _save_upload(value, workspace, max_size)
            elif name == METADATA_FIELD and metadata is None:
                metadata = value
            else:
                raise ValidationFailed(f'unexpected form field {name!r}, options belong in {METADATA_FIELD!r}')

    data = _decode_object(metadata, METADATA_FIELD) if metadata else {}
    if 'source' not in data and DEFAULT_PART in parts:
        data['source'] = f'{PART_SCHEME}://{DEFAULT_PART}'
    return data, parts


async def _save_upload(upload: UploadFile, workspace: Workspace, max_size: int) -> Path:
    path = workspace.new_file(upload.filename or DEFAULT_PART)
    written = 0
    with path.open('wb') as file:
        while chunk := await upload.read(_CHUNK):
            written += len(chunk)
            if written > max_size:
                raise SourceTooLarge(f'upload exceeds {max_size} bytes')
            file.write(chunk)
    return path


def _decode_object(raw: str | bytes, field: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ValidationFailed(f'{field} is not valid JSON') from e
    if not isinstance(data, dict):
        raise ValidationFailed(f'{field} must be a JSON object')
    return data


def openapi_body(model: type[BaseModel]) -> dict[str, Any]:
    """Documents both transports; the request is parsed manually so FastAPI cannot infer it"""
    return {
        'requestBody': {
            'required': True,
            'content': {
                'application/json': {'schema': inline_refs(model.model_json_schema())},
                'multipart/form-data': {
                    'schema': {
                        'type': 'object',
                        'properties': {
                            METADATA_FIELD: {
                                'type': 'string',
                                'description': f'JSON-encoded {model.__name__}; source defaults to file://file',
                            },
                            DEFAULT_PART: {'type': 'string', 'format': 'binary'},
                        },
                    }
                },
            },
        }
    }


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    definitions = schema.pop('$defs', {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if ref := node.get('$ref'):
                return resolve(definitions[ref.rsplit('/', 1)[-1]])
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)
