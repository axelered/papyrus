from dataclasses import dataclass
from typing import Self

from server.config import WorkersConfig
from server.services.detect import DetectService
from server.services.markdown import MarkdownService


@dataclass
class Services:
    detect: DetectService
    markdown: MarkdownService

    @classmethod
    def create(cls, workers: WorkersConfig) -> Self:
        # todo: per-service processes, timeouts and max_tasks are code heuristics, make them configurable in YAML
        return cls(detect=DetectService(), markdown=MarkdownService(processes=workers.processes))

    def close(self) -> None:
        self.markdown.close()
