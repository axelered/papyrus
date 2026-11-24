import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ByteSize,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from server.utils.logs import LogFormat

DEFAULT_CONFIG_PATH = Path('/etc/papyrus/config.yml')

_CONNECTOR_NAME = r'^[a-z0-9][a-z0-9_-]*$'
# browsers send Origin as scheme://host[:port], a trailing slash or path never matches
_ORIGIN = r'^https?://[^/\s]+$'
_URL = r'^https?://\S+$'


class ConfigError(Exception):
    pass


class _Section(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class WorkersConfig(_Section):
    processes: PositiveInt = Field(default_factory=lambda: os.cpu_count() or 1)
    max_queue: PositiveInt = 64
    warmup: bool = True

    @field_validator('processes', mode='before')
    @classmethod
    def _resolve_auto(cls, value: Any) -> Any:
        return (os.cpu_count() or 1) if value == 'auto' else value


class LimitsConfig(_Section):
    max_file_size: ByteSize = ByteSize(100 * 1000 * 1000)
    deadline: PositiveFloat = 300
    tmp_dir: Path = Field(default_factory=lambda: Path(tempfile.gettempdir()) / 'papyrus')


class HttpConfig(_Section):
    enabled: bool = True
    allow_private: bool = False
    allowed_hosts: tuple[str, ...] = ()
    timeout: PositiveFloat = 30
    max_redirects: int = Field(default=5, ge=0)
    # BCP 47 language asked of remote sites by the fetcher and the browser, None lets the site decide
    locale: str | None = Field(default='en-US', pattern=r'^[a-zA-Z]{2,3}(-[a-zA-Z]{4})?(-([a-zA-Z]{2}|\d{3}))?$')

    @field_validator('allowed_hosts')
    @classmethod
    def _lower_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(host.lower() for host in value)


class BrowserConfig(_Section):
    # off by default: the Chromium sandbox needs user namespaces, which default seccomp profiles block (adr/browser.md)
    sandbox: bool = False


class _OcrConfig(_Section):
    # per container, replicas multiply it
    concurrency: PositiveInt = 8
    max_pages: PositiveInt = 100


class QwenConfig(_OcrConfig):
    type: Literal['qwen']
    model: str = Field(min_length=1)
    url: str = Field(pattern=_URL)
    api_key: SecretStr | None = Field(default=None, min_length=1)
    extra_body: dict[str, Any] = Field(default_factory=dict)


class MistralConfig(_OcrConfig):
    type: Literal['mistral']
    model: str = Field(default='mistral-ocr-latest', min_length=1)
    url: str | None = Field(default=None, pattern=_URL)
    api_key: SecretStr = Field(min_length=1)


OcrConfig = Annotated[QwenConfig | MistralConfig, Field(discriminator='type')]


class CorsConfig(_Section):
    # empty keeps the browser same-origin policy, ['*'] allows any web page that holds a key
    allow_origins: tuple[str, ...] = ()

    @field_validator('allow_origins')
    @classmethod
    def _check_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if '*' in value and len(value) > 1:
            raise ValueError("'*' cannot be combined with other origins")
        invalid = [origin for origin in value if origin != '*' and not re.match(_ORIGIN, origin)]
        if invalid:
            raise ValueError(f'invalid origins {invalid}, expected scheme://host[:port] without a path')
        return value


class ConnectorConfig(_Section):
    protocol: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class Config(_Section):
    workers: WorkersConfig = Field(default_factory=WorkersConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    # None disables OCR: no document leaves the container
    ocr: OcrConfig | None = None
    cors: CorsConfig = Field(default_factory=CorsConfig)
    connectors: dict[str, ConnectorConfig] = Field(default_factory=dict)

    @field_validator('connectors')
    @classmethod
    def _check_connector_names(cls, value: dict[str, ConnectorConfig]) -> dict[str, ConnectorConfig]:
        invalid = [name for name in value if not re.match(_CONNECTOR_NAME, name)]
        if invalid:
            raise ValueError(f'invalid connector names {invalid}, expected {_CONNECTOR_NAME}')
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='PAPYRUS_', env_file='.env', extra='ignore', frozen=True)

    config: Path = DEFAULT_CONFIG_PATH
    api_key: str = ''
    auth_disabled: bool = False
    # Server-Timing header and a log line per request with stage durations, for profiling only
    timing: bool = False
    # papyrus loggers only: uvicorn keeps --log-level, libraries stay at warning
    log_level: Literal['debug', 'info', 'warning', 'error'] = 'info'
    # json for log pipelines that parse lines (OTEL collectors, Loki, Cloud Logging)
    log_format: LogFormat = 'text'

    @field_validator('log_level', 'log_format', mode='before')
    @classmethod
    def _lower(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @property
    def api_keys(self) -> tuple[str, ...]:
        return tuple(key.strip() for key in self.api_key.split(',') if key.strip())

    def check(self) -> None:
        if not self.auth_disabled and not self.api_keys:
            raise ConfigError('PAPYRUS_API_KEY is not set (set PAPYRUS_AUTH_DISABLED=true for local development)')
