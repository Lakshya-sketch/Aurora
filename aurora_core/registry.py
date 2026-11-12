"""Plugin discovery + load for Aurora v2.

Discovery rules (see architecture.md §3 and implementation_plan.md
Phase 0):

  * Each subdirectory of ``plugins.root`` is a plugin package.
  * The module that declares the plugin is ``<subdir>/plugin.py``.
  * That module must export a single ``BasePlugin`` subclass, or a
    ``PLUGIN`` symbol pointing to one. (We support both to make
    "drop a folder in" as painless as possible.)
  * Discovery respects ``plugins.enabled`` and ``plugins.disabled``
    lists from the config. ``disabled`` wins.

Plugin folders are imported as ``aurora_plugin_<subdir>`` so they
are not picked up by ordinary ``import`` statements — this avoids
side-effects at import time elsewhere in the codebase.

If a plugin fails to import, behaviour depends on
``plugins.on_error``: ``"safe"`` (default) logs a warning and
skips; ``"strict"`` raises and crashes the kernel.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Iterable

from aurora_core.plugin_base import BasePlugin

logger = logging.getLogger("aurora.registry")


class RegistryError(RuntimeError):
    """Raised when plugin loading fails under ``on_error: strict``."""


def _iter_plugin_dirs(plugins_root: Path) -> list[Path]:
    """Return immediate subdirectories of ``plugins_root`` that look like plugins."""
    if not plugins_root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(plugins_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(("_", ".")):
            continue
        if not (child / "plugin.py").is_file():
            continue
        out.append(child)
    return out


def _load_one(plugin_dir: Path) -> BasePlugin | None:
    """Import ``plugin.py`` inside ``plugin_dir`` and return the plugin instance.

    Returns ``None`` if no plugin symbol can be located.
    """
    mod_name = f"aurora_plugin_{plugin_dir.name}"
    spec = importlib.util.spec_from_file_location(
        mod_name, plugin_dir / "plugin.py"
    )
    if spec is None or spec.loader is None:
        logger.error("could not build import spec for %s", plugin_dir)
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        logger.exception("failed to import %s", plugin_dir / "plugin.py")
        return None

    # Look for a PLUGIN symbol first (explicit export).
    candidate = getattr(module, "PLUGIN", None)
    if candidate is None:
        # Fall back to the first BasePlugin subclass we can find.
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is BasePlugin:
                continue
            if issubclass(obj, BasePlugin):
                candidate = obj
                break

    if candidate is None:
        logger.error("no BasePlugin subclass / PLUGIN symbol in %s", plugin_dir)
        return None

    try:
        return candidate()  # type: ignore[abstract]
    except Exception:
        logger.exception("failed to instantiate plugin from %s", plugin_dir)
        return None


def _filter_by_lists(
    discovered: list[str],
    enabled: Iterable[str],
    disabled: Iterable[str],
) -> list[str]:
    enabled_set = {n.strip() for n in enabled if n and n.strip()}
    disabled_set = {n.strip() for n in disabled if n and n.strip()}
    picked = [n for n in discovered if n not in disabled_set]
    if enabled_set:
        picked = [n for n in picked if n in enabled_set]
    return picked


def discover_plugins(
    plugins_root: Path,
    *,
    enabled: list[str] | None = None,
    disabled: list[str] | None = None,
    on_error: str = "safe",
) -> list[BasePlugin]:
    """Discover and instantiate plugins under ``plugins_root``.

    Args:
        plugins_root: The ``plugins/`` directory in the repo.
        enabled: If non-empty, only these plugin folder names are loaded.
        disabled: Always-skipped plugin folder names (takes precedence).
        on_error: ``"safe"`` (skip + log) or ``"strict"`` (raise).

    Returns:
        List of instantiated plugins, in deterministic order.
    """
    enabled = enabled or []
    disabled = disabled or []

    if on_error not in {"safe", "strict"}:
        raise RegistryError(
            f"plugins.on_error must be 'safe' or 'strict', got {on_error!r}"
        )

    discovered_dirs = _iter_plugin_dirs(plugins_root)
    discovered_names = [d.name for d in discovered_dirs]

    kept_names = _filter_by_lists(discovered_names, enabled, disabled)
    kept_set = set(kept_names)

    plugins: list[BasePlugin] = []
    for d in discovered_dirs:
        if d.name not in kept_set:
            logger.info("plugin skipped (filtered): %s", d.name)
            continue
        try:
            instance = _load_one(d)
        except Exception as exc:
            msg = f"failed to load plugin {d.name}: {exc}"
            if on_error == "strict":
                raise RegistryError(msg) from exc
            logger.warning("skipping plugin %s: %s", d.name, exc)
            continue
        if instance is None:
            msg = f"no plugin symbol found in {d.name}/plugin.py"
            if on_error == "strict":
                raise RegistryError(msg)
            logger.warning("skipping plugin %s: %s", d.name, "no symbol found")
            continue
        plugins.append(instance)
        logger.info("plugin loaded: %s v%s", instance.name, instance.version)

    return plugins
