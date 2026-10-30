"""Read `config.yaml`, overlay the environment and validate, or explain what is wrong."""

import logging
from importlib.resources import files
from typing import Any

import yaml
from pydantic import SecretStr, ValidationError

from remotebackups.config.env import EnvSettings
from remotebackups.config.schema import Config
from remotebackups.paths import ConfigPaths

logger = logging.getLogger(__name__)

EXAMPLE_TEXT = (files("remotebackups.config") / "config.example.yaml").read_text(encoding="utf-8")


class ConfigError(Exception):
    """One or more problems with the configuration, each already phrased for a human."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def load_config(paths: ConfigPaths, env: EnvSettings) -> Config:
    """The effective configuration: defaults, then the file, then the environment."""
    file_values = _read_config_file(paths)
    merged = _deep_merge(file_values, env.config_overrides())
    try:
        return Config.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(format_validation_errors(exc)) from exc


def ensure_config_files(paths: ConfigPaths) -> bool:
    """Create config.yaml from the commented example when missing.

    Returns True when it was created, so the caller can tell the user. The example itself
    is not written to /config: `print-example-config` prints the current one on demand.
    """
    if paths.config_file.exists():
        return False
    paths.config_file.write_text(EXAMPLE_TEXT, encoding="utf-8")
    logger.warning(
        "No config.yaml found; wrote a commented example to %s. Add your folders and rerun",
        paths.config_file,
    )
    return True


def dump_config(config: Config, *, redact: bool) -> str:
    """The effective configuration as YAML, secrets masked unless asked otherwise."""
    data = config.model_dump(mode="json" if redact else "python")
    return yaml.safe_dump(_reveal_secrets(data), sort_keys=False, allow_unicode=True)


def format_validation_errors(exc: ValidationError) -> list[str]:
    problems = []
    for error in exc.errors():
        path = _dotted(error["loc"])
        if error["type"] == "extra_forbidden":
            problems.append(f"{path}: unknown key")
        elif error["type"] == "missing":
            problems.append(f"{path}: is required")
        else:
            message = error["msg"].removeprefix("Value error, ")
            shown = _shown_input(error.get("input"))
            problems.append(f"{path}: {message}{shown}")
    return problems


def _read_config_file(paths: ConfigPaths) -> dict[str, Any]:
    if not paths.config_file.exists():
        return {}
    try:
        loaded = yaml.safe_load(paths.config_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError([f"{paths.config_file}: invalid YAML: {exc}"]) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError([f"{paths.config_file}: the top level must be a mapping of sections"])
    return loaded


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _dotted(loc: tuple[int | str, ...]) -> str:
    text = ""
    for part in loc:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else part)
    return text or "config"


def _shown_input(value: Any) -> str:
    if value is None or isinstance(value, dict | list | SecretStr):
        return ""
    return f" (got {value!r})"


def _reveal_secrets(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, dict):
        return {key: _reveal_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_reveal_secrets(item) for item in value]
    if hasattr(value, "value"):  # StrEnum members dump as themselves in python mode
        return value.value
    return value
