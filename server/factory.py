import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Self

from fastapi import FastAPI

from server import version
from server.api import health, inspect, markdown, render, screenshot
from server.api.admission import Admission
from server.config import Config, ConfigError, Settings
from server.io.connectors import Connectors
from server.io.http import HttpFetcher
from server.io.transfer import Transfer
from server.services.base import Service
from server.services.browser import BrowserService
from server.services.convert import ConvertService
from server.services.detect import DetectService
from server.services.markdown import MarkdownService
from server.services.ocr import OcrService
from server.services.render import RenderService
from server.utils.cors import CorsMiddleware
from server.utils.logs import RequestIdMiddleware
from server.utils.openapi import install_openapi
from server.utils.problem import install_problem_handlers
from server.utils.timing import TimingMiddleware
from server.utils.yaml import load_yaml

LOGGER_NAMESPACE = 'papyrus'
# bounds a hung LibreOffice or Chromium launch at boot, same as the worker task timeout
WARMUP_TIMEOUT = 120

logger = logging.getLogger(f'{LOGGER_NAMESPACE}.startup')


@dataclass(frozen=True)
class Topology:
    """Worker processes per pool, concurrent browser pages and OCR renders, derived from `workers.processes`"""

    markdown: int
    convert: int
    render: int
    browser: int
    ocr_renders: int

    @classmethod
    def of(cls, config: Config) -> Self:
        # todo: per-service processes, timeouts and max_tasks are code heuristics, make them configurable in YAML
        processes = config.workers.processes
        # LibreOffice workers weigh about 330 MB each, each open page 50 to 150 MB
        half = max(1, processes // 2)
        # OCR shares the render pool, a slow document must not take all of it
        return cls(markdown=processes, convert=half, render=processes, browser=half, ocr_renders=half)


@dataclass
class Services:
    """Enters every service on startup and closes them in reverse order on shutdown"""

    detect: DetectService
    markdown: MarkdownService
    convert: ConvertService
    render: RenderService
    browser: BrowserService
    ocr: OcrService
    _stack: AsyncExitStack = field(default_factory=AsyncExitStack, init=False, repr=False)

    @classmethod
    def create(cls, config: Config, topology: Topology) -> Self:
        return cls(
            detect=DetectService(),
            markdown=MarkdownService(processes=topology.markdown),
            convert=ConvertService(processes=topology.convert, profiles=config.limits.tmp_dir / 'lo-profiles'),
            render=RenderService(processes=topology.render),
            browser=BrowserService(config.http, config.browser, concurrency=topology.browser),
            ocr=OcrService(config.ocr, renders=topology.ocr_renders),
        )

    async def __aenter__(self) -> Self:
        for service in self._all():
            await self._stack.enter_async_context(service)
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._stack.aclose()

    async def warm(self, timeout: float = WARMUP_TIMEOUT) -> None:
        """Warms every service concurrently; the first failure cancels the others and fails startup"""
        services = self._named()
        pending = set(services)
        logger.info('warming %s', ', '.join(services))
        started = time.monotonic()

        async def warm_one(name: str, service: Service) -> None:
            began = time.monotonic()
            try:
                await service.warm()
            except Exception:
                logger.exception('%s warmup failed', name)
                raise
            pending.discard(name)
            logger.info('%s warm in %.2fs', name, time.monotonic() - began)

        try:
            async with asyncio.timeout(timeout), asyncio.TaskGroup() as group:
                for name, service in services.items():
                    group.create_task(warm_one(name, service))
        except TimeoutError:
            raise TimeoutError(f'warmup exceeded {timeout:g}s, waiting on {", ".join(sorted(pending))}') from None
        logger.info('services warm in %.2fs', time.monotonic() - started)

    def _all(self) -> tuple[Service, ...]:
        return tuple(self._named().values())

    def _named(self) -> dict[str, Service]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.init}


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
        'pools markdown=%s convert=%s render=%s processes, browser=%s pages sandbox=%s warmup=%s',
        topology.markdown,
        topology.convert,
        topology.render,
        topology.browser,
        _on(config.browser.sandbox),
        _on(config.workers.warmup),
    )
    if ocr := config.ocr:
        logger.info(
            'ocr type=%s model=%s concurrency=%s max_pages=%s', ocr.type, ocr.model, ocr.concurrency, ocr.max_pages
        )
    else:
        logger.info('ocr off')
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
        topology = Topology.of(resolved_config)
        log_startup(resolved_settings, resolved_config, topology)
        async with AsyncExitStack() as stack:
            max_file_size = int(resolved_config.limits.max_file_size)
            http = HttpFetcher(resolved_config.http, max_file_size)
            stack.push_async_callback(http.aclose)
            app.state.transfer = Transfer(Connectors(resolved_config.connectors), http, max_file_size)
            services = Services.create(resolved_config, topology)
            app.state.services = await stack.enter_async_context(services)
            if resolved_config.workers.warmup:
                await services.warm()
            else:
                logger.info('warmup off, services start on first use')
            yield

    app = FastAPI(title='Axelered Papyrus', version=version.__version__, lifespan=lifespan)
    install_openapi(app)
    # added first so it runs inside RequestIdMiddleware and the timing log line carries the request id
    app.add_middleware(TimingMiddleware, enabled=lambda: app.state.settings.timing)
    app.add_middleware(RequestIdMiddleware)
    # outermost: preflights are answered before routing and auth, and every response carries the CORS headers
    app.add_middleware(CorsMiddleware, origins=lambda: app.state.config.cors.allow_origins)
    install_problem_handlers(app, logging.getLogger(f'{LOGGER_NAMESPACE}.errors'))
    app.include_router(health.router)
    app.include_router(markdown.router)
    app.include_router(render.router)
    app.include_router(screenshot.router)
    app.include_router(inspect.router)
    return app
