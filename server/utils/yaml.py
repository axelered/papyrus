import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

_ENV_PLACEHOLDER = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')


def load_yaml(path: Path, env: Mapping[str, str]) -> dict[str, Any]:
    """YAML mapping with ${VAR} expanded from `env`; an empty file is an empty mapping, every failure a ValueError"""
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except yaml.YAMLError as e:
        raise ValueError(f'invalid YAML in {path}: {e}') from e
    if not isinstance(data, dict):
        raise ValueError(f'root of {path} must be a mapping')
    return expand_env(data, env)


def expand_env(value: Any, env: Mapping[str, str]) -> Any:
    """Expands ${VAR} in parsed string values (never in raw YAML, so env values cannot inject YAML syntax)"""
    if isinstance(value, str):
        return _ENV_PLACEHOLDER.sub(lambda match: _lookup(match.group(1), env), value)
    if isinstance(value, dict):
        return {key: expand_env(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_env(item, env) for item in value]
    return value


def _lookup(name: str, env: Mapping[str, str]) -> str:
    if name not in env:
        raise ValueError(f'environment variable {name} is not set')
    return env[name]
