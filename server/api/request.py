import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from server.config import Config
from server.errors import DeadlineExceeded, UnsupportedUri
from server.io.uri import PART_SCHEME
from server.io.workspace import Workspace
from server.types.common import OpRequest
from server.utils.body import JSON, MULTIPART, read_body
from server.utils.openapi import inline_refs
from server.utils.timing import stage

DEFAULT_PART = 'file'
METADATA_FIELD = 'metadata'

_MAX_FILE_PARTS = 8


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
                with stage('parse'):
                    op = await parse_op(request, model, workspace, int(config.limits.max_file_size))
                if op.model.destination:
                    request.app.state.transfer.check_destination(op.model.destination)
                yield op
        except TimeoutError as e:
            if deadline.expired():
                raise DeadlineExceeded(f'request exceeded {config.limits.deadline}s') from e
            raise


async def parse_op[M: OpRequest](request: Request, model: type[M], workspace: Workspace, max_size: int) -> Op[M]:
    body = await read_body(
        request,
        new_file=workspace.new_file,
        data_field=METADATA_FIELD,
        max_file_size=max_size,
        max_files=_MAX_FILE_PARTS,
    )
    data = body.data
    if body.content_type == MULTIPART and 'source' not in data and DEFAULT_PART in body.files:
        data['source'] = f'{PART_SCHEME}://{DEFAULT_PART}'
    try:
        parsed = model.model_validate(data)
    except ValidationError as e:
        raise RequestValidationError(e.errors()) from e
    if body.content_type == JSON and parsed.source.startswith(f'{PART_SCHEME}://'):
        raise UnsupportedUri('file:// sources require multipart/form-data')
    return Op(model=parsed, parts=body.files, workspace=workspace)


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
