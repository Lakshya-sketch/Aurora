"""File search handler — disk file search + text content reader.

Searches a directory tree for files matching a glob pattern.
Optionally reads text content of matched files (capped at 4 KB per file).

Ignores heavy system directories (Windows, Program Files, node_modules, AppData, etc.)
and automatically narrows drive root searches (C:\\) to the user's home directory
to ensure instant responses without timing out.
"""

from __future__ import annotations

import fnmatch
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("aurora.tools.file_search")

MAX_CONTENT_BYTES = 4096   # 4 KB per file content read
MAX_RESULTS = 50           # cap to avoid huge payloads

# Directories to skip for speed and safety
IGNORED_DIRS = {
    "Windows", "Program Files", "Program Files (x86)", "ProgramData",
    "AppData", "$Recycle.Bin", "System Volume Information", "Recovery",
    "node_modules", ".git", "venv", "__pycache__", ".vscode", ".idea",
}


async def handle(args: dict[str, Any]) -> dict[str, Any]:
    """File search tool entry point.

    Args:
        args: dict with keys:
            - directory (str, required): path to search in.
            - pattern   (str, required): glob pattern e.g. '*.py' or 'resume*'.
            - recursive (bool, default True): search subdirectories.
            - read_content (bool, default False): read file text content.

    Returns:
        ToolResult envelope.
    """
    directory    = str(args.get("directory", "")).strip()
    pattern      = str(args.get("pattern", "*")).strip()
    recursive    = bool(args.get("recursive", True))
    read_content = bool(args.get("read_content", False))

    if not directory:
        return {
            "ok": False,
            "data": None,
            "error": "directory parameter is required. For the current codebase, use 'E:/NeuralChat2'.",
        }
    if not pattern:
        pattern = "*"

    search_root = Path(directory).expanduser().resolve()

    # If search_root does not exist and model hallucinated a dummy CI/runner path, redirect to project root
    if not search_root.exists() and ("runneradmin" in str(search_root).lower() or "github" in str(search_root).lower() or "orchestrator" in str(search_root).lower()):
        project_root = Path("E:/NeuralChat2").resolve()
        logger.info("file_search: redirecting non-existent path '%s' to active project root '%s'", search_root, project_root)
        search_root = project_root

    # FIX: If search_root is drive root (e.g. C:\ or /), default to user's home directory
    # so we don't scan 500,000 system files and time out.
    if search_root.parent == search_root or str(search_root) in ("C:\\", "C:/", "/"):
        logger.info("file_search: narrowing drive root '%s' to user home '%s'", search_root, Path.home())
        search_root = Path.home()

    if not search_root.exists():
        return {
            "ok":    False,
            "data":  None,
            "error": f"Directory not found: {search_root}. Note: The active codebase is located at 'E:/NeuralChat2'.",
        }
    if not search_root.is_dir():
        return {
            "ok":    False,
            "data":  None,
            "error": f"Path is not a directory: {search_root}",
        }

    logger.info(
        "file_search: dir=%s pattern=%r recursive=%s read_content=%s",
        search_root, pattern, recursive, read_content,
    )

    matched_paths: list[Path] = []

    try:
        if not recursive:
            for item in search_root.glob(pattern):
                if item.is_file():
                    matched_paths.append(item)
                    if len(matched_paths) >= MAX_RESULTS:
                        break
        else:
            # Fast os.walk skipping IGNORED_DIRS
            for root, dirs, files in os.walk(search_root):
                # Prune ignored directories in-place
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]

                for filename in files:
                    if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(filename.lower(), pattern.lower()):
                        full_p = Path(root) / filename
                        matched_paths.append(full_p)
                        if len(matched_paths) >= MAX_RESULTS:
                            break
                if len(matched_paths) >= MAX_RESULTS:
                    break
    except Exception as exc:
        return {"ok": False, "data": None, "error": f"Search error: {exc}"}

    # Sort for deterministic output
    matched_paths = sorted(matched_paths[:MAX_RESULTS])

    results = []
    for file_path in matched_paths:
        try:
            st = file_path.stat()
            entry: dict[str, Any] = {
                "path":       str(file_path),
                "size_bytes": st.st_size,
                "content":    None,
            }
            if read_content and file_path.is_file():
                try:
                    raw = file_path.read_bytes()[:MAX_CONTENT_BYTES]
                    entry["content"] = raw.decode("utf-8", errors="replace")
                    if st.st_size > MAX_CONTENT_BYTES:
                        entry["content"] += f"\n... [truncated at {MAX_CONTENT_BYTES} bytes]"
                except Exception as exc:
                    entry["content"] = f"[read error: {exc}]"
            results.append(entry)
        except OSError:
            continue

    logger.info("file_search: found %d files", len(results))
    return {
        "ok": True,
        "data": {
            "matches":   results,
            "count":     len(results),
            "truncated": len(matched_paths) >= MAX_RESULTS,
        },
        "error": None,
    }
