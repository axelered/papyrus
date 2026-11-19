import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.types import Receive, Scope, Send

from server.api.request import Op
from server.errors import (
    DeadlineExceeded,
    DestinationFailed,
    ProcessingFailed,
    ProcessingTimeout,
    QueueFull,
    SourceFetchFailed,
    SourceForbidden,
    SourceNotFound,
    SourceTooLarge,
    Unauthorized,
    UnknownConnector,
    UnsupportedUri,
)
from server.io.transfer import Materialized
from server.io.workspace import Workspace
from server.services.detect import Document, lookup
from server.types.common import Encoding, Fetch, FormatLabel, ImageFormat, OpResponse, OutputMode, Result, Source
from server.utils.openapi import problem_responses
from server.utils.problem import (
    BadRequest,
    PayloadTooLarge,
    ProblemError,
    UnsupportedMediaType,
    ValidationFailed,
)
from server.utils.timing import stage

_ENVELOPE = frozenset({'source', 'options', 'result'})

# errors every operation can return: transport, auth, admission, source and destination, deadline (adr/api.md)
OP_ERRORS: tuple[type[ProblemError], ...] = (
    BadRequest,
    Unauthorized,
    SourceForbidden,
    SourceNotFound,
    PayloadTooLarge,
    SourceTooLarge,
    UnsupportedMediaType,
    ValidationFailed,
    UnsupportedUri,
    UnknownConnector,
    ProcessingFailed,
    ProblemError,
    SourceFetchFailed,
    DestinationFailed,
    QueueFull,
    ProcessingTimeout,
    DeadlineExceeded,
)
IMAGE_FILES = {format.mime_type: {'schema': {'type': 'string', 'format': 'binary'}} for format in ImageFormat}


def source_of(uri: str, name: str, document: Document, materialized: Materialized, **facts: Any) -> Source:
    """`name` is the materialized name, before detection renames the file to its detected extension"""
    value = document.format
    fetched = materialized.fetched
    return Source(
        uri=uri,
        name=name,
        size=document.path.stat().st_size,
        mime_type=value.mime_type,
        extension=value.extension,
        label=value.label,
        group=value.group,
        fetch=Fetch(url=fetched.url, status=fetched.status) if fetched else None,
        **facts,
    )


def result_of(path: Path, label: str, **facts: Any) -> Result:
    """Facts of a produced file; its type comes from the format table, never from detection"""
    value = lookup(label)
    return Result(
        name=path.name,
        size=path.stat().st_size,
        mime_type=value.mime_type,
        extension=value.extension,
        label=value.label,
        group=value.group,
        **facts,
    )


def op_responses(files: Mapping[str, dict[str, Any]], *errors: type[ProblemError]) -> dict[int | str, dict[str, Any]]:
    """OpenAPI responses of an operation: the JSON envelope comes from `response_model`, `files` are the op files"""
    return {
        200: {
            'description': 'The JSON envelope (light with a destination), or the op file for `output=file`',
            'content': dict(files),
            'headers': {
                'Content-Disposition': {'description': 'File name, for `output=file`', 'schema': {'type': 'string'}}
            },
        },
        **problem_responses(*OP_ERRORS, *errors),
    }


class WorkspaceFileResponse(FileResponse):
    """Removes the workspace once the file is streamed, including when the client disconnects mid-stream"""

    def __init__(self, path: Path, media_type: str, workspace: Workspace):
        super().__init__(path, media_type=media_type, filename=path.name)
        self._workspace = workspace
        workspace.keep()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._workspace.cleanup()


async def respond(request: Request, op: Op, response: OpResponse, artifact: Path, encoding: Encoding) -> Response:
    """Builds the reply for `response`, whose result describes `artifact` without content (adr/api.md, Output Modes)

    The op file is the artifact, or the full JSON document when the response has data sections.
    """
    has_data = _has_data(response)
    inline = op.model.destination is None and op.model.output == OutputMode.JSON
    if has_data or inline:
        update = {'encoding': encoding, 'content': _content(artifact, encoding)}
        response.result = response.result.model_copy(update=update)
    if has_data:
        path = op.workspace.new_file(f'{artifact.stem}.json')
        path.write_text(response.model_dump_json(exclude_none=True), encoding='utf-8')
        file, media_type = result_of(path, FormatLabel.JSON), 'application/json'
    else:
        path, file, media_type = artifact, response.result, _media_type(response.result.mime_type, encoding)

    if op.model.destination:
        with stage('deliver'):
            await request.app.state.transfer.deliver(path, op.model.destination)
        light = file.model_copy(update={'uri': op.model.destination, 'encoding': None, 'content': None})
        response = type(response).model_validate({**_envelope(response), 'result': light})
        return _json(response)
    if not inline:
        return WorkspaceFileResponse(path, media_type, op.workspace)
    return _json(response)


def _content(path: Path, encoding: Encoding) -> str:
    if encoding == Encoding.UTF8:
        return path.read_text(encoding='utf-8')
    return base64.b64encode(path.read_bytes()).decode('ascii')


def _media_type(mime_type: str, encoding: Encoding) -> str:
    return f'{mime_type}; charset=utf-8' if encoding == Encoding.UTF8 and mime_type.startswith('text/') else mime_type


def _has_data(response: OpResponse) -> bool:
    return any(getattr(response, name) is not None for name in type(response).model_fields if name not in _ENVELOPE)


def _envelope(response: OpResponse) -> dict[str, Any]:
    """The three fixed keys only; data sections stay behind with the heavy content"""
    return {name: getattr(response, name) for name in _ENVELOPE}


def _json(response: OpResponse) -> JSONResponse:
    return JSONResponse(response.model_dump(mode='json', exclude_none=True))
