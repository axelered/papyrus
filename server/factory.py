from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from server.api import health
from server.api.admission import Admission
from server.config import Config, Settings, load_config
from server.errors import install_error_handlers
from server.io.connectors import Connectors
from server.io.transfer import Transfer
from server.logs import RequestIdMiddleware, configure_logging


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
        app.state.transfer = Transfer(Connectors(resolved_config.connectors), int(resolved_config.limits.max_file_size))
        yield

    configure_logging()
    app = FastAPI(title='Axelered Papyrus', lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    return app
