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


class WaitUntil(StrEnum):
    """Navigation milestone awaited before the capture; values are Playwright wait states"""

    LOAD = 'load'
    DOMCONTENTLOADED = 'domcontentloaded'
    NETWORKIDLE = 'networkidle'


class ScreenshotOptions(OptionsModel):
    width: int = Field(default=1280, ge=320, le=3840, description='Viewport width in CSS pixels')
    height: int = Field(default=800, ge=200, le=2160, description='Viewport height in CSS pixels')
    full_page: bool = Field(default=False, description=f'Capture the whole page, cut at {MAX_IMAGE_SIDE} px height')
    scale: int = Field(default=1, ge=1, le=3, description='Device scale factor, multiplies the output pixels')
    wait_until: WaitUntil = Field(
        default=WaitUntil.LOAD,
        description='Page event awaited before the capture: `load`, `domcontentloaded` or `networkidle`',
    )
    delay: int = Field(default=0, ge=0, le=10_000, description='Milliseconds to wait after `wait_until`')
    format: ImageFormat = Field(default=ImageFormat.PNG, description=FORMAT)

    @model_validator(mode='after')
    def _bounded_output(self) -> Self:
        if max(self.width, self.height) * self.scale > MAX_IMAGE_SIDE:
            raise ValueError(f'viewport times scale must not exceed {MAX_IMAGE_SIDE} px per side')
        return self


class ScreenshotRequest(OpRequest):
    source: str = Field(description='http(s):// URL of the page, navigated by the browser')
    output: OutputMode = Field(default=OutputMode.FILE, description=OUTPUT)
    options: ScreenshotOptions = Field(default_factory=ScreenshotOptions, description=OPTIONS)


class ScreenshotResponse(OpResponse):
    options: ScreenshotOptions = Field(description=RESOLVED)
