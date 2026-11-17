from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.types.common import MAX_IMAGE_SIDE, ImageFormat, OpRequest, OutputMode, SourceType


class Fit(StrEnum):
    """How a page fills the thumbnail box"""

    CONTAIN = 'contain'
    COVER = 'cover'


class ThumbnailOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')

    page: int = Field(default=1, ge=1, description='Page, or frame of an animated or multi-page image')
    width: int = Field(default=256, ge=1, le=MAX_IMAGE_SIDE, description='Box width in pixels')
    height: int = Field(default=256, ge=1, le=MAX_IMAGE_SIDE, description='Box height in pixels')
    fit: Fit = Field(default=Fit.CONTAIN, description='`cover` fills the box and center crops')
    format: ImageFormat = ImageFormat.WEBP


class RenderOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')

    page: int = Field(default=1, ge=1, description='Page, or frame of an animated or multi-page image')
    width: int | None = Field(default=None, ge=1, le=MAX_IMAGE_SIDE, description='Width in pixels')
    height: int | None = Field(default=None, ge=1, le=MAX_IMAGE_SIDE, description='Height in pixels')
    dpi: int | None = Field(
        default=None,
        ge=1,
        le=600,
        description='Resolution of document pages, 150 when no size is set; images ignore it',
    )
    format: ImageFormat = ImageFormat.PNG

    @model_validator(mode='after')
    def _one_sizing(self) -> Self:
        if self.dpi is not None and (self.width is not None or self.height is not None):
            raise ValueError('dpi cannot be combined with width or height')
        return self


class ThumbnailRequest(OpRequest):
    output: OutputMode = OutputMode.FILE
    options: ThumbnailOptions = Field(default_factory=ThumbnailOptions)


class RenderRequest(OpRequest):
    output: OutputMode = OutputMode.FILE
    options: RenderOptions = Field(default_factory=RenderOptions)


class RenderResult(BaseModel):
    data: str = Field(description='Base64 encoded image')
    content_type: str
    width: int = Field(description='Image width in pixels')
    height: int = Field(description='Image height in pixels')
    page: int
    page_count: int | None = Field(default=None, description='Omitted for office and HTML input')
    source_type: SourceType
