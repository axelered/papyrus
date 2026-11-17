import asyncio
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from fastapi import FastAPI

from server import version
from server.api import health, markdown, render, screenshot
from server.api.admission import Admission
from server.config import Config, ConfigError, Settings
from server.io.connectors import Connectors
from server.io.http import HttpFetcher
from server.io.transfer import Transfer
from server.services.browser import BrowserService
from server.services.convert import ConvertService
from server.services.detect import DetectService
from server.services.markdown import MarkdownService
from server.services.render import RenderService
from server.utils.logs import RequestIdMiddleware, configure_logging
from server.utils.problem import install_problem_handlers
from server.utils.timing import TimingMiddleware
from server.utils.yaml import load_yaml

LOGGER_NAMESPACE = 'papyrus'

logger = logging.getLogger(f'{LOGGER_NAMESPACE}.startup')


@dataclass(frozen=True)
class Topology:
    """Worker processes per pool and concurrent browser pages, derived from `workers.processes`"""

    markdown: int
    convert: int
    render: int
    browser: int

    @classmethod
    def of(cls, config: Config) -> Self:
        # todo: per-service processes, timeouts and max_tasks are code heuristics, make them configurable in YAML
        processes = config.workers.processes
        # LibreOffice workers weigh about 330 MB each, each open page 50 to 150 MB
        half = max(1, processes // 2)
        return cls(markdown=processes, convert=half, render=processes, browser=half)


@dataclass
class Services:
    detect: DetectService
    markdown: MarkdownService
    convert: ConvertService
    render: RenderService
    browser: BrowserService

    @classmethod
    def create(cls, config: Config, topology: Topology) -> Self:
        return cls(
            detect=DetectService(),
            markdown=MarkdownService(processes=topology.markdown),
            convert=ConvertService(processes=topology.convert, profiles=config.limits.tmp_dir / 'lo-profiles'),
            render=RenderService(processes=topology.render),
            browser=BrowserService(config.http, config.browser, concurrency=topology.browser),
        )

    async def aclose(self) -> None:
        await self.browser.aclose()
        # pool joins block
        await asyncio.to_thread(self._close_pools)

    def _close_pools(self) -> None:
        self.markdown.close()
        self.convert.close()
        self.render.close()


def load_config(path: Path, env: Mapping[str, str] | None = None) -> Config:
    """Missing file means all defaults; ${VAR} placeholders come from `env` (process env by default)"""
    if not path.exists():
        return Config()
    try:
        data = load_yaml(path, os.environ if env is None else env)
    except ValueError as e:
        raise ConfigError(str(e)) from e
    return Config.model_validate(data)


def log_startup(settings: Settings, config: Config, topology: Topology) -> None:
    """What this process runs, from config only: never keys, never connector options (they hold credentials)"""
    limits, http = config.limits, config.http
    watermark = f' ({version.__watermark__})' if version.__watermark__ else ''
    source = settings.config if settings.config.exists() else f'{settings.config} (missing, defaults)'
    logger.info('papyrus %s%s, config %s', version.__version__, watermark, source)
    logger.info(
        'admission max_queue=%s deadline=%gs max_file_size=%s tmp_dir=%s',
        config.workers.max_queue,
        limits.deadline,
        limits.max_file_size.human_readable(decimal=True),
        limits.tmp_dir,
    )
    logger.info(
        'pools (started on first use) markdown=%s convert=%s render=%s processes, browser=%s pages sandbox=%s',
        topology.markdown,
        topology.convert,
        topology.render,
        topology.browser,
        _on(config.browser.sandbox),
    )
    hosts = ','.join(http.allowed_hosts) or 'any'
    connectors = ','.join(f'{name}:{c.protocol}' for name, c in config.connectors.items()) or 'none'
    logger.info(
        'sources http=%s allow_private=%s allowed_hosts=%s connectors=%s',
        _on(http.enabled),
        _on(http.allow_private),
        hosts,
        connectors,
    )
    auth = 'off' if settings.auth_disabled else f'on ({len(settings.api_keys)} keys)'
    logger.info('auth=%s timing=%s', auth, _on(settings.timing))


def _on(value: bool) -> str:
    return 'on' if value else 'off'


def create_app(settings: Settings | None = None, config: Config | None = None) -> FastAPI:
    """Settings and config are loaded at startup (not import) so importing the package has no side effects"""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings = settings or Settings()
        resolved_settings.check()
        resolved_config = config or load_config(resolved_settings.config)

        app.state.settings = resolved_settings
        app.state.config = resolved_config
        app.state.admission = Admission(resolved_config.workers.max_queue)
        max_file_size = int(resolved_config.limits.max_file_size)
        http = HttpFetcher(resolved_config.http, max_file_size)
        app.state.transfer = Transfer(Connectors(resolved_config.connectors), http, max_file_size)
        topology = Topology.of(resolved_config)
        log_startup(resolved_settings, resolved_config, topology)
        app.state.services = services = Services.create(resolved_config, topology)
        try:
            yield
        finally:
            await http.aclose()
            await services.aclose()

    configure_logging(LOGGER_NAMESPACE)
    app = FastAPI(title='Axelered Papyrus', lifespan=lifespan)
    # added first so it runs inside RequestIdMiddleware and the timing log line carries the request id
    app.add_middleware(TimingMiddleware, enabled=lambda: app.state.settings.timing)
    app.add_middleware(RequestIdMiddleware)
    install_problem_handlers(app, logging.getLogger(f'{LOGGER_NAMESPACE}.errors'))
    app.include_router(health.router)
    app.include_router(markdown.router)
    app.include_router(render.router)
    app.include_router(screenshot.router)
    return app
