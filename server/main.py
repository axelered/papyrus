from server.config import Settings
from server.factory import LOGGER_NAMESPACE, create_app
from server.utils.logs import configure_logging

# ASGI entrypoint `server.main:app`; kept out of `server/__init__.py` so pool workers do not build the app.
# Logging is set up here, after uvicorn configured its loggers on startup and before it logs the server start.
settings = Settings()
configure_logging(LOGGER_NAMESPACE, settings.log_level, settings.log_format)
app = create_app(settings=settings)
