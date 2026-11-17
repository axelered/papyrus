from collections.abc import Callable
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.types import Receive, Scope, Send

from server.api.request import Op
from server.io.workspace import Workspace
from server.services.detect import Format
from server.types.common import OutputMode, Receipt, SourceType


def source_type_of(value: Format) -> SourceType:
    return SourceType(mime_type=value.mime_type, extension=value.extension, label=value.label, group=value.group)


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
