"""Entry point: ``python -m aurora_core``.

Phase 1: load config, bring up the kernel, wire the transport app,
and run Hypercorn until SIGINT.

  python -m aurora_core            # serve
  python -m aurora_core --smoke    # one-shot test (legacy Phase 0 mode)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Make ``config/`` discoverable from repo root without install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from aurora_core.config import load_config  # noqa: E402
from aurora_core.kernel import Kernel, KernelError  # noqa: E402
from aurora_core.transport import wire_app  # noqa: E402

logger = logging.getLogger("aurora.main")


async def _smoke() -> int:
    """Legacy Phase 0 mode: discover, setup, publish test.event, exit."""
    from aurora_core.transport import app as quart_app  # noqa: F401

    config_path = _REPO_ROOT / "config" / "aurora.yaml"
    if not config_path.is_file():
        print(f"[kernel] FATAL: config not found at {config_path}", file=sys.stderr)
        return 2
    cfg = load_config(config_path)
    log_level = str(cfg.get("aurora", {}).get("log_level", "INFO")).upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    kernel = Kernel(cfg)
    try:
        await kernel.startup()
    except KernelError as exc:
        print(f"[kernel] startup failed: {exc}", file=sys.stderr)
        return 3
    await kernel.bus.publish("test.event", {"from": "python -m aurora_core --smoke"})
    await asyncio.sleep(0.2)
    print("[kernel] summary:", kernel.summary())
    await kernel.shutdown()
    return 0


async def _serve() -> int:
    """Phase 1 mode: start Hypercorn, run forever (until SIGINT)."""
    # Imported lazily so the legacy --smoke path doesn't pull Quart.
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    from aurora_core.transport import app as quart_app  # noqa: F401

    config_path = _REPO_ROOT / "config" / "aurora.yaml"
    if not config_path.is_file():
        print(f"[kernel] FATAL: config not found at {config_path}", file=sys.stderr)
        return 2
    cfg = load_config(config_path)

    # Logger: INFO in console, also tee to data/aurora.log once we know the path.
    log_level = str(cfg.get("aurora", {}).get("log_level", "INFO")).upper()
    log_handlers: list[logging.Handler] = [
        logging.StreamHandler(),
    ]
    # Use the *resolved* log_dir (not the raw ${AURORA_LOG_DIR:-...} token)
    # by reading the kernel config dict that load_config returns. The
    # data dir is required for the runtime to work, so error loud if missing.
    aurora_root = Path(cfg.get("aurora", {}).get("home") or ".")
    log_dir = Path(cfg.get("aurora", {}).get("log_dir") or (aurora_root / "data"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "aurora.log"
        log_handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except OSError as exc:
        print(f"[kernel] WARN: could not open log file {log_dir / 'aurora.log'}: {exc}", file=sys.stderr)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=log_handlers,
        force=True,
    )

    # Bring the kernel up.
    kernel = Kernel(cfg)
    try:
        await kernel.startup()
    except KernelError as exc:
        print(f"[kernel] startup failed: {exc}", file=sys.stderr)
        return 3

    # Wire the Quart app to the running kernel.
    wire_app(kernel)
    logger.info("transport: wired Quart app to kernel")

    # Hypercorn config from aurora.yaml transport block.
    host = str(cfg.get("transport", {}).get("host", "127.0.0.1"))
    port = int(cfg.get("transport", {}).get("port", 8765))
    hyper_cfg = Config()
    hyper_cfg.bind = [f"{host}:{port}"]
    hyper_cfg.worker_class = "asyncio"

    logger.info(
        "aurora: serving on http://%s:%d  (ws ws://%s:%d/stream)",
        host, port, host, port,
    )
    print(f"[aurora] listening on http://{host}:{port}", flush=True)
    try:
        await serve(quart_app, hyper_cfg)
    finally:
        await kernel.shutdown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora_core")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="one-shot Phase 0 smoke test (default: serve forever)",
    )
    args = parser.parse_args()
    if args.smoke:
        return asyncio.run(_smoke())
    return asyncio.run(_serve())


if __name__ == "__main__":
    raise SystemExit(main())
