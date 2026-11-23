from pydantic import Field

from server.types.common import OPTIONS, RESOLVED, ApiModel, OptionsModel, Source, SourceRequest


class InspectOptions(OptionsModel):
    """No options yet; kept so later checks can add switches without changing the shape"""


class InspectChecks(ApiModel):
    """Every check is `true` when passed, `false` when failed and omitted when it does not apply"""

    extension_match: bool | None = Field(
        default=None,
        description=(
            'The name extension declares the detected format or a generic parent of it; omitted without an '
            'extension. For http(s) URLs without one, the extension comes from the response `Content-Type`'
        ),
    )


class InspectRequest(SourceRequest):
    options: InspectOptions = Field(default_factory=InspectOptions, description=OPTIONS)


class InspectResponse(ApiModel):
    """No artifact is produced, so there is no `result` (adr/inspect.md)"""

    source: Source = Field(description='The input document')
    options: InspectOptions = Field(description=RESOLVED)
    checks: InspectChecks = Field(description='Checks run on the source')
