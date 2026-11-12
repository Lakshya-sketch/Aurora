"""Code execution handler — Python sandbox via subprocess.

Runs Python code in a **child subprocess** using the same venv Python
interpreter. The subprocess is isolated from the Aurora kernel process.

Safety model (per AGENTS.md Rule 6):
  * Subprocess tracked with a handle — killed on timeout.
  * stdout + stderr captured and returned in envelope.
  * Resource cap: 10s default timeout, 64 KB output cap.
  * No network restrictions at this phase (Phase 3.0 scope is sandbox
    execution, not security hardening).

Returns:
    ToolResult envelope::

        {
          "ok": true,
          "data": {
            "stdout": "...",
            "stderr": "...",
            "exit_code": 0,
            "timed_out": false
          },
          "error": null
        }
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("aurora.tools.code_exec")

DEFAULT_TIMEOUT_S = 10
MAX_OUTPUT_BYTES  = 65536  # 64 KB cap on stdout+stderr combined


async def handle(args: dict[str, Any]) -> dict[str, Any]:
    """Code execution tool entry point.

    Args:
        args: dict with keys:
            - code      (str, required): Python source code to run.
            - timeout_s (int, default 10): max execution time in seconds.

    Returns:
        ToolResult envelope.
    """
    code      = args.get("code", "").strip()
    timeout_s = int(args.get("timeout_s", DEFAULT_TIMEOUT_S))

    if not code:
        return {"ok": False, "data": None, "error": "code is required"}

    if timeout_s < 1 or timeout_s > 60:
        timeout_s = DEFAULT_TIMEOUT_S

    logger.info("code_exec: running %d-char snippet with timeout=%ds", len(code), timeout_s)

    # Write code to a temporary file so the subprocess can import it cleanly
    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(code)
        tmp_path = Path(tmp.name)

    try:
        # FIX: Use sys.executable so we always run in the active venv Python,
        # not a potentially different system Python on PATH.
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout_s)
            )
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            stdout_bytes, stderr_bytes = await proc.communicate()

        # Cap output size to avoid huge payloads
        stdout = stdout_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = stderr_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        exit_code = proc.returncode if proc.returncode is not None else -1

        logger.info(
            "code_exec: exit_code=%d timed_out=%s stdout_len=%d stderr_len=%d",
            exit_code, timed_out, len(stdout), len(stderr),
        )

        return {
            "ok": True,
            "data": {
                "stdout":    stdout,
                "stderr":    stderr,
                "exit_code": exit_code,
                "timed_out": timed_out,
            },
            "error": None,
        }

    except Exception as exc:
        logger.error("code_exec: subprocess error: %s", exc, exc_info=True)
        return {"ok": False, "data": None, "error": str(exc)}

    finally:
        # Always clean up the temp file (AGENTS.md Rule 6)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
