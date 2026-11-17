import asyncio
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from server.api import health, markdown
from server.api.admission import Admission
from server.config import Config, ConfigError, Settings
from server.io.connectors import Connectors
from server.io.http import HttpFetcher
from server.io.transfer import Transfer
from server.services.registry import Services
from server.utils.logs import RequestIdMiddleware, configure_logging
from server.utils.problem import install_problem_handlers
from server.utils.yaml import load_yaml

LOGGER_NAMESPACE = 'papyrus'


def load_config(path: Path, env: Mapping[str, str] | None = None) -> Config:
    """Missing file means all defaults; ${VAR} placeholders come from `env` (process env by default)"""
    if not path.exists():
        return Config()
    try:
        data = load_yaml(path, os.environ if env is None else env)
    except ValueError as e:
        raise ConfigError(str(e)) from e
    return Config.model_validate(data)


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
        app.state.services = services = Services.create(resolved_config.workers)
        try:
            yield
        finally:
            await http.aclose()
            await asyncio.to_thread(services.close)

    configure_logging(LOGGER_NAMESPACE)
    app = FastAPI(title='Axelered Papyrus', lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    install_problem_handlers(app, logging.getLogger(f'{LOGGER_NAMESPACE}.errors'))
    app.include_router(health.router)
    app.include_router(markdown.router)
    return app
