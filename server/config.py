import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ByteSize, ConfigDict, Field, PositiveFloat, PositiveInt, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from server.utils.logs import LogFormat

DEFAULT_CONFIG_PATH = Path('/etc/papyrus/config.yml')

_CONNECTOR_NAME = r'^[a-z0-9][a-z0-9_-]*$'


class ConfigError(Exception):
    pass


class _Section(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class WorkersConfig(_Section):
    processes: PositiveInt = Field(default_factory=lambda: os.cpu_count() or 1)
    max_queue: PositiveInt = 64

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

    @field_validator('allowed_hosts')
    @classmethod
    def _lower_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(host.lower() for host in value)


class BrowserConfig(_Section):
    # off by default: the Chromium sandbox needs user namespaces, which default seccomp profiles block (adr/browser.md)
    sandbox: bool = False


class ConnectorConfig(_Section):
    protocol: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class Config(_Section):
    workers: WorkersConfig = Field(default_factory=WorkersConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
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
