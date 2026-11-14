"""Terminal handler — safe shell command execution on Windows.

Runs a shell command via PowerShell (``powershell.exe -Command``) with
a configurable timeout. Captures stdout + stderr and returns them.

Safety model (per AGENTS.md Rules 6, 9):
  * Subprocess tracked with a handle — killed on timeout.
  * Default 30s timeout, max 120s.
  * Blocklist of destructive patterns (rm -rf, format, etc.) refused
    without user confirmation flag.
  * stdout + stderr capped at 64 KB combined.

Per AGENTS.md Rule 7: Windows is the target. PowerShell is used as the
shell because it is always present on Windows 10+.

Returns:
    ToolResult envelope::

        {
          "ok": true,
          "data": {
            "stdout":    "...",
            "stderr":    "...",
            "exit_code": 0,
            "timed_out": false,
            "command":   "..."
          },
          "error": null
        }
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger("aurora.tools.terminal")

DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S     = 120
MAX_OUTPUT_BYTES  = 65536  # 64 KB

# ---------------------------------------------------------------------------
# Blocklist — patterns that require explicit confirmed=True from caller
# ---------------------------------------------------------------------------
_DANGEROUS_PATTERNS = re.compile(
    r"(rm\s+-rf|del\s+/[sqf]|format\s+[a-z]:|\brmdir\b.*\/[sq]|"
    r"Remove-Item.*-Recurse.*-Force|Clear-Disk|diskpart)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Windows System File Guard
# Strictly protects operating system files, system directories, and registry
# ---------------------------------------------------------------------------
_PROTECTED_SYS_DIR_REGEX = re.compile(
    r"""(?xi)
    # Windows Directory and system folders
    ([a-z]:[\\/]+(windows|winnt)\b) |
    (%systemroot%|%windir%|\$env:systemroot|\$env:windir)\b |
    # Program Files
    ([a-z]:[\\/]+program\s+files(\s*\(x86\))?\b) |
    (%programfiles%|%programfiles\(x86\)%|\$env:programfiles|\$env:programfiles\(x86\))\b |
    # ProgramData Windows core
    ([a-z]:[\\/]+programdata[\\/]+microsoft[\\/]+windows\b) |
    # Boot files & pagefiles
    ([a-z]:[\\/]+(bootmgr|pagefile\.sys|hiberfil\.sys|swapfile\.sys)\b) |
    # Registry hives
    \b(hklm|hkcr|registry::hkey_local_machine|registry::hkey_classes_root)\b
    """,
)

_MUTATION_OPERATORS_REGEX = re.compile(
    r"""(?xi)
    # File write / creation / deletion cmdlets
    \b(set-content|out-file|add-content|remove-item|clear-content|move-item|copy-item|new-item|rename-item)\b |
    # Classic CLI file deletion/alteration
    \b(del|erase|rmdir|rd|ren|rename|move|copy)\b |
    # Permission manipulation & security overrides
    \b(takeown|icacls|cacls|attrib)\b |
    # Registry modifications
    \b(reg(\.exe)?\s+(add|delete|import|restore))\b |
    \b(set-itemproperty|remove-itemproperty|new-itemproperty)\b |
    # Redirection operators writing to file
    (>|>>)\s*["']?
    """,
)


def validate_system_file_guard(command: str, cwd: str | None = None) -> tuple[bool, str | None]:
    """Inspect shell command to prevent tampering with protected Windows system directories.

    Returns:
        (is_safe: bool, rejection_reason: str | None)
    """
    # 1. Check if cwd is set to a protected system directory with mutation commands
    if cwd:
        cwd_match = _PROTECTED_SYS_DIR_REGEX.search(cwd)
        if cwd_match and _MUTATION_OPERATORS_REGEX.search(command):
            return False, (
                f"Access Denied: Working directory is set to a protected Windows system directory ({cwd!r}). "
                "Modification operations are strictly blocked by Aurora Windows System File Guard."
            )

    # 2. Check if the command targets a protected system directory with a mutation or write operator
    sys_match = _PROTECTED_SYS_DIR_REGEX.search(command)
    if sys_match:
        if _MUTATION_OPERATORS_REGEX.search(command):
            target = sys_match.group(0)
            return False, (
                f"Access Denied: Command attempts to create, modify, or delete files inside protected "
                f"Windows system location ({target!r}). Action blocked by Aurora Windows System File Guard."
            )

    return True, None


async def handle(args: dict[str, Any]) -> dict[str, Any]:
    """Terminal tool entry point.

    Args:
        args: dict with keys:
            - command   (str, required): shell command to run.
            - cwd       (str, optional): working directory.
            - timeout_s (int, default 30): max execution time.
            - confirmed (bool, default False): bypass dangerous check.

    Returns:
        ToolResult envelope.
    """
    command   = args.get("command", "").strip()
    cwd       = args.get("cwd") or None
    timeout_s = min(int(args.get("timeout_s", DEFAULT_TIMEOUT_S)), MAX_TIMEOUT_S)
    confirmed = bool(args.get("confirmed", False))

    if not command:
        return {"ok": False, "data": None, "error": "command is required"}

    # 1. Windows System File Guard (Hard Barrier — Non-bypassable)
    is_safe, guard_error = validate_system_file_guard(command, cwd)
    if not is_safe:
        logger.warning("terminal: blocked by Windows System File Guard: %s", guard_error)
        return {
            "ok":    False,
            "data":  None,
            "error": guard_error,
        }

    # 2. Safety gate — refuse dangerous commands unless user explicitly confirmed
    if _DANGEROUS_PATTERNS.search(command) and not confirmed:
        return {
            "ok":    False,
            "data":  None,
            "error": (
                f"Command contains a dangerous pattern and requires explicit confirmation. "
                f"Re-submit with confirmed=True if you are sure: {command!r}"
            ),
        }

    logger.info(
        "terminal: executing command=%r cwd=%r timeout=%ds",
        command, cwd, timeout_s,
    )

    try:
        # FIX: Use powershell.exe -NoProfile -Command to avoid loading user
        # profile (slow + unpredictable env) while keeping Unicode support.
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
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

        stdout    = stdout_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr    = stderr_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        exit_code = proc.returncode if proc.returncode is not None else -1

        logger.info(
            "terminal: exit_code=%d timed_out=%s stdout_len=%d stderr_len=%d",
            exit_code, timed_out, len(stdout), len(stderr),
        )

        return {
            "ok": True,
            "data": {
                "stdout":    stdout,
                "stderr":    stderr,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "command":   command,
            },
            "error": None,
        }

    except FileNotFoundError:
        # FIX: powershell.exe not found — should never happen on Win10+,
        # but surface it clearly rather than a raw traceback.
        return {
            "ok":    False,
            "data":  None,
            "error": "powershell.exe not found. This tool requires Windows 10+.",
        }
    except Exception as exc:
        logger.error("terminal: subprocess error: %s", exc, exc_info=True)
        return {"ok": False, "data": None, "error": str(exc)}
