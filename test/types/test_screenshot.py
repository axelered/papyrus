import pytest
from pydantic import ValidationError

from server.types.common import ImageFormat, OutputMode
from server.types.screenshot import ScreenshotOptions, ScreenshotRequest


@pytest.mark.parametrize(
    ('format', 'mime_type', 'extension'),
    [
        (ImageFormat.PNG, 'image/png', 'png'),
        (ImageFormat.JPEG, 'image/jpeg', 'jpg'),
        (ImageFormat.WEBP, 'image/webp', 'webp'),
    ],
)
def test_image_format_attributes(format: ImageFormat, mime_type: str, extension: str):
    assert (format.mime_type, format.extension) == (mime_type, extension)


def test_defaults_return_a_file():
    request = ScreenshotRequest(source='https://example.com')

    assert request.output == OutputMode.FILE
    assert (request.options.width, request.options.height, request.options.scale) == (1280, 800, 1)


@pytest.mark.parametrize(
    'options',
    [{'width': 319}, {'height': 2161}, {'scale': 4}, {'delay': 10_001}, {'width': 2049, 'scale': 2}, {'unknown': 1}],
)
def test_rejects_out_of_bounds_options(options: dict):
    with pytest.raises(ValidationError):
        ScreenshotOptions.model_validate(options)


def test_scale_fits_max_side():
    assert ScreenshotOptions(width=2048, height=1024, scale=2).scale == 2
