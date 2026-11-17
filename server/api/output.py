from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.types import Receive, Scope, Send

from server.api.request import Op
from server.io.workspace import Workspace
from server.services.detect import Format
from server.types.common import ImageFormat, OutputMode, Receipt, SourceType
from server.utils.openapi import inline_refs


def source_type_of(value: Format) -> SourceType:
    return SourceType(mime_type=value.mime_type, extension=value.extension, label=value.label, group=value.group)


def image_responses(result: type[BaseModel]) -> dict[int | str, dict[str, Any]]:
    """OpenAPI 200 response of operations producing one image: the file, the JSON result or a receipt"""
    return {
        200: {
            'description': 'Image as a file, as JSON, or a receipt when `destination` is set',
            'content': {
                'application/json': {
                    'schema': {
                        'anyOf': [inline_refs(result.model_json_schema()), inline_refs(Receipt.model_json_schema())]
                    }
                },
                **{format.mime_type: {'schema': {'type': 'string', 'format': 'binary'}} for format in ImageFormat},
            },
        }
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


async def respond(
    request: Request,
    op: Op,
    path: Path,
    media_type: str,
    as_json: Callable[[], BaseModel],
    source_type: SourceType | None = None,
) -> Response:
    """Builds the response for the result at `path` according to the request's destination and output"""
    if op.model.destination:
        size = await request.app.state.transfer.deliver(path, op.model.destination)
        receipt = Receipt(destination=op.model.destination, content_type=media_type, size=size, source_type=source_type)
        return JSONResponse(receipt.model_dump(mode='json', exclude_none=True))
    if op.model.output == OutputMode.FILE:
        return WorkspaceFileResponse(path, media_type, op.workspace)
    return JSONResponse(as_json().model_dump(mode='json', exclude_unset=True))
