from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from server.types.common import (
    FORMAT,
    MAX_IMAGE_SIDE,
    OPTIONS,
    OUTPUT,
    RESOLVED,
    ImageFormat,
    OpRequest,
    OpResponse,
    OptionsModel,
    OutputMode,
)

DEFAULT_DPI = 150


class Fit(StrEnum):
    """How a page fills the thumbnail box"""

    CONTAIN = 'contain'
    COVER = 'cover'


class ThumbnailOptions(OptionsModel):
    page: int = Field(default=1, ge=1, description='Page, or frame of an animated or multi-page image')
    width: int = Field(default=256, ge=1, le=MAX_IMAGE_SIDE, description='Box width in pixels')
    height: int = Field(default=256, ge=1, le=MAX_IMAGE_SIDE, description='Box height in pixels')
    fit: Fit = Field(default=Fit.CONTAIN, description='`cover` fills the box and center crops')
    format: ImageFormat = Field(default=ImageFormat.WEBP, description=FORMAT)


class RenderOptions(OptionsModel):
    page: int = Field(default=1, ge=1, description='Page, or frame of an animated or multi-page image')
    width: int | None = Field(default=None, ge=1, le=MAX_IMAGE_SIDE, description='Width in pixels')
    height: int | None = Field(default=None, ge=1, le=MAX_IMAGE_SIDE, description='Height in pixels')
    dpi: int | None = Field(
        default=None,
        ge=1,
        le=600,
        description=f'Resolution of document pages, {DEFAULT_DPI} when no size is set; images ignore it',
    )
    format: ImageFormat = Field(default=ImageFormat.PNG, description=FORMAT)

    @model_validator(mode='after')
    def _one_sizing(self) -> Self:
        if self.dpi is not None and (self.width is not None or self.height is not None):
            raise ValueError('dpi cannot be combined with width or height')
        if self.dpi is None and self.width is None and self.height is None:
            # resolved here so the response echoes the effective value
            self.dpi = DEFAULT_DPI
        return self


class ThumbnailRequest(OpRequest):
    output: OutputMode = Field(default=OutputMode.FILE, description=OUTPUT)
    options: ThumbnailOptions = Field(default_factory=ThumbnailOptions, description=OPTIONS)


class RenderRequest(OpRequest):
    output: OutputMode = Field(default=OutputMode.FILE, description=OUTPUT)
    options: RenderOptions = Field(default_factory=RenderOptions, description=OPTIONS)


class ThumbnailResponse(OpResponse):
    options: ThumbnailOptions = Field(description=RESOLVED)


class RenderResponse(OpResponse):
    options: RenderOptions = Field(description=RESOLVED)
