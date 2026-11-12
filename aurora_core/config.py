"""Configuration loader for Aurora v2.

Reads ``config/aurora.yaml`` and overlays environment variables from
``.env`` (loaded via ``python-dotenv``). YAML values may reference
environment variables as ``${VAR_NAME}`` strings, which are resolved
at load time.

We deliberately do NOT use ``os.environ`` directly for config keys —
the YAML file is the source of truth, and env vars only fill in
values that the YAML author chose to leave templated. This keeps
config diffable in git while still allowing per-machine overrides.

See AGENTS.md rule #1 ("Never hardcode paths") and the
implementation_plan.md Phase 0 deliverable for ``aurora_core/config.py``.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger("aurora.config")

# Match ${VAR} or ${VAR:-default} for innermost tokens (where default has no nested ${).
# Innermost-first resolution allows recursive expansion of nested defaults like ${A:-${B:-fallback}}.
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-((?:(?!\$\{)[^}])*))?\}")


class ConfigError(RuntimeError):
    """Raised when the YAML is malformed or a required env var is missing."""


def _resolve_env(value: Any) -> Any:
    """Recursively replace ${VAR} tokens in strings.

    Each string is processed by a fixed-point loop, so nested
    defaults like ``${A:-${B:-fallback}}`` resolve correctly.
    """
    if isinstance(value, str):
        return _resolve_string(value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def _replace(match: re.Match[str]) -> str:
    var, default = match.group(1), match.group(2)
    raw = os.environ.get(var)
    if raw is not None and raw != "":
        return raw
    if default is None:
        raise ConfigError(
            f"env var ${var} referenced in config is unset and has no default"
        )
    # Return the default verbatim. The fixed-point loop in
    # ``_resolve_string`` (called from ``_resolve_env``) re-scans the
    # entire string each iteration, so this default will be expanded
    # on the next pass if it itself contains ${...} tokens.
    return default


def _resolve_string(value: str) -> str:
    """Resolve ${VAR} tokens in a string until it stops changing.

    Bounded iterations cap runaway expansion (e.g. cycle).
    """
    prev: str | None = None
    cur = value
    for _ in range(8):
        if cur == prev:
            return cur
        prev = cur
        cur = _ENV_RE.sub(_replace, cur)
    raise ConfigError("env-var substitution did not converge; check for cycles")


def load_config(
    config_path: str | Path,
    *,
    dotenv_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load YAML config with env-var resolution.

    Args:
        config_path: Path to ``config/aurora.yaml``.
        dotenv_path: Optional explicit path to a ``.env`` file. If
            ``None``, looks for ``.env`` next to the config file.

    Returns:
        The resolved config dict.

    Raises:
        ConfigError: on missing file, malformed YAML, or unresolved env vars.
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")

    # Load .env (silent if absent — the file is optional).
    if dotenv_path is None:
        dotenv_path = config_path.parent.parent / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)

    with config_path.open("r", encoding="utf-8") as fh:
        try:
            raw = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"malformed YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(
            f"top-level YAML in {config_path} must be a mapping, got {type(raw).__name__}"
        )

    return _resolve_env(raw)


def get(cfg: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Nested-dict lookup helper. Returns ``default`` if any key is missing.

    Example:
        >>> get(cfg, "aurora", "log_level", default="INFO")
    """
    cur: Any = cfg
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur
