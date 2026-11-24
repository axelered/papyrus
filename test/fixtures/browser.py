from pathlib import Path

from server.services.browser import Capture
from server.types.screenshot import ScreenshotOptions
from server.utils.problem import ProblemError

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 24


class FakeBrowser:
    def __init__(self, error: ProblemError | None = None):
        self.error = error
        self.calls: list[tuple[str, str, ScreenshotOptions]] = []

    async def screenshot(self, url: str, target: Path, options: ScreenshotOptions) -> Capture:
        self.calls.append((url, target.name, options))
        if self.error:
            raise self.error
        target.write_bytes(PNG)
        return Capture(width=options.width, height=options.height, url=f'{url}final', status=200)

    async def aclose(self) -> None:
        pass
