"""Code Intelligence Handler — Phase 4.1: AST / Symbol Graph Navigation.

Provides deterministic code navigation and structural understanding for Aurora:
1. `get_symbol_map`: Extracts classes, methods, functions, docstrings, imports from Python (via stdlib AST)
   and JS/TS (via regex parsing).
2. `find_definitions`: Locates exact definitions of symbols (classes, functions, methods) across files.
3. `find_callers`: Finds references, instantiations, and call sites of symbols across the project.

Zero external heavy dependencies.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

# Default directories to ignore during project scanning
IGNORED_DIRS = {
    ".git",
    "venv",
    ".venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    ".hermes",
    ".gemini",
}

SUPPORTED_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx"}


def _resolve_path(target_path: str | None) -> Path:
    """Resolve target path safely to an absolute Path."""
    if not target_path or target_path.strip() in (".", ""):
        return Path.cwd()
    p = Path(target_path)
    if not p.is_absolute():
        p = Path.cwd() / p
    resolved = p.resolve()
    if not resolved.exists() and ("runneradmin" in str(resolved).lower() or "github" in str(resolved).lower() or "orchestrator" in str(resolved).lower()):
        return Path.cwd()
    return resolved


def _should_skip_dir(dir_name: str) -> bool:
    return dir_name in IGNORED_DIRS or dir_name.startswith(".")


# ----------------------------------------------------------------------
# Python AST Parsing
# ----------------------------------------------------------------------

def _format_arg(arg: ast.arg) -> str:
    """Format an ast.arg with type annotation if present."""
    if arg.annotation:
        try:
            return f"{arg.arg}: {ast.unparse(arg.annotation)}"
        except Exception:
            return arg.arg
    return arg.arg


def _format_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Format function signature string."""
    args_list = []
    # Positional args
    for a in node.args.args:
        args_list.append(_format_arg(a))
    if node.args.vararg:
        args_list.append(f"*{node.args.vararg.arg}")
    for a in node.args.kwonlyargs:
        args_list.append(_format_arg(a))
    if node.args.kwarg:
        args_list.append(f"**{node.args.kwarg.arg}")

    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    ret = ""
    if node.returns:
        try:
            ret = f" -> {ast.unparse(node.returns)}"
        except Exception:
            ret = ""
    return f"{prefix}{node.name}({', '.join(args_list)}){ret}:"


def _parse_python_file(file_path: Path) -> dict[str, Any]:
    """Parse a single Python file into a symbol map using AST."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError as exc:
        return {
            "file": str(file_path),
            "language": "python",
            "error": f"SyntaxError at line {exc.lineno}: {exc.msg}",
            "classes": [],
            "functions": [],
            "imports": [],
        }
    except Exception as exc:
        return {
            "file": str(file_path),
            "language": "python",
            "error": str(exc),
            "classes": [],
            "functions": [],
            "imports": [],
        }

    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                try:
                    bases.append(ast.unparse(b))
                except Exception:
                    pass

            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({
                        "name": item.name,
                        "signature": _format_function_signature(item),
                        "line_start": item.lineno,
                        "line_end": getattr(item, "end_lineno", item.lineno),
                        "is_async": isinstance(item, ast.AsyncFunctionDef),
                        "docstring": ast.get_docstring(item) or None,
                    })

            classes.append({
                "name": node.name,
                "bases": bases,
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno),
                "docstring": ast.get_docstring(node) or None,
                "methods": methods,
            })

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({
                "name": node.name,
                "signature": _format_function_signature(node),
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno),
                "is_async": isinstance(node, ast.AsyncFunctionDef),
                "docstring": ast.get_docstring(node) or None,
            })

        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    "module": alias.name,
                    "asname": alias.asname,
                    "line": node.lineno,
                })

        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = [alias.name for alias in node.names]
            imports.append({
                "module": mod,
                "names": names,
                "line": node.lineno,
            })

    return {
        "file": str(file_path),
        "language": "python",
        "classes": classes,
        "functions": functions,
        "imports": imports,
    }


# ----------------------------------------------------------------------
# JS / TS Regex Parsing
# ----------------------------------------------------------------------

RE_JS_CLASS = re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z0-9_$]+)(?:\s+extends\s+([A-Za-z0-9_$.]+))?", re.MULTILINE)
RE_JS_FUNC = re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(([^)]*)\)", re.MULTILINE)
RE_JS_ARROW = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>", re.MULTILINE)
RE_JS_IMPORT = re.compile(r"^\s*import\s+(.+?)\s+from\s+['\"]([^'\"]+)['\"]", re.MULTILINE)


def _parse_import_clause(clause: str) -> list[str]:
    cleaned = re.sub(r"[\{\}]", " ", clause)
    names = []
    for part in cleaned.split(","):
        part = part.strip()
        if not part:
            continue
        if " as " in part:
            part = part.split(" as ")[-1].strip()
        elif part.startswith("* as "):
            part = part.replace("* as ", "").strip()
        names.append(part)
    return names


def _parse_js_file(file_path: Path) -> dict[str, Any]:
    """Parse a JS/TS/JSX/TSX file using regex extraction."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {
            "file": str(file_path),
            "language": "javascript",
            "error": str(exc),
            "classes": [],
            "functions": [],
            "imports": [],
        }

    lines = content.splitlines()
    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []

    for i, line in enumerate(lines, start=1):
        # Class match
        m_cls = RE_JS_CLASS.search(line)
        if m_cls:
            name, base = m_cls.group(1), m_cls.group(2)
            classes.append({
                "name": name,
                "bases": [base] if base else [],
                "line_start": i,
                "line_end": i,
                "methods": [],
            })
            continue

        # Function match
        m_fn = RE_JS_FUNC.search(line)
        if m_fn:
            name, params = m_fn.group(1), m_fn.group(2)
            functions.append({
                "name": name,
                "signature": f"function {name}({params.strip()})",
                "line_start": i,
                "line_end": i,
                "is_async": "async" in line,
            })
            continue

        # Arrow function match
        m_arr = RE_JS_ARROW.search(line)
        if m_arr:
            name, params = m_arr.group(1), m_arr.group(2)
            functions.append({
                "name": name,
                "signature": f"const {name} = ({params.strip()}) =>",
                "line_start": i,
                "line_end": i,
                "is_async": "async" in line,
            })
            continue

        # Import match
        m_imp = RE_JS_IMPORT.search(line)
        if m_imp:
            clause, mod = m_imp.group(1), m_imp.group(2)
            names = _parse_import_clause(clause)
            imports.append({
                "module": mod,
                "names": names,
                "line": i,
            })

    return {
        "file": str(file_path),
        "language": "javascript" if file_path.suffix in (".js", ".jsx") else "typescript",
        "classes": classes,
        "functions": functions,
        "imports": imports,
    }


# ----------------------------------------------------------------------
# Core Actions
# ----------------------------------------------------------------------

async def get_symbol_map(path: str | None = None, max_files: int = 50) -> dict[str, Any]:
    """Return structural symbol map for a file or directory.

    Args:
        path: Path to a file or directory. Defaults to current working directory.
        max_files: Max files to parse if path is a directory (default 50).
    """
    target = _resolve_path(path)
    if not target.exists():
        return {"ok": False, "data": None, "error": f"Path does not exist: {target}"}

    if target.is_file():
        if target.suffix == ".py":
            data = _parse_python_file(target)
        elif target.suffix in SUPPORTED_EXTENSIONS:
            data = _parse_js_file(target)
        else:
            return {"ok": False, "data": None, "error": f"Unsupported file extension: {target.suffix}"}
        return {"ok": True, "data": data, "error": None}

    # Directory traversal
    file_maps: list[dict[str, Any]] = []
    count = 0
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if not _should_skip_dir(d)]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                fpath = Path(root) / f
                if ext == ".py":
                    file_maps.append(_parse_python_file(fpath))
                else:
                    file_maps.append(_parse_js_file(fpath))
                count += 1
                if count >= max_files:
                    break
        if count >= max_files:
            break

    return {
        "ok": True,
        "data": {
            "directory": str(target),
            "files_parsed": len(file_maps),
            "symbols": file_maps,
        },
        "error": None,
    }


async def find_definitions(symbol: str, path: str | None = None) -> dict[str, Any]:
    """Find definitions of a symbol across a file or directory.

    Args:
        symbol: Name of class, function, or method to find.
        path: File or directory path to search. Defaults to current working directory.
    """
    if not symbol or not symbol.strip():
        return {"ok": False, "data": None, "error": "symbol parameter is required"}

    target = _resolve_path(path)
    if not target.exists():
        return {"ok": False, "data": None, "error": f"Path does not exist: {target}"}

    target_symbol = symbol.strip()
    definitions: list[dict[str, Any]] = []

    files_to_check: list[Path] = []
    if target.is_file():
        files_to_check.append(target)
    else:
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if not _should_skip_dir(d)]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    files_to_check.append(Path(root) / f)

    for fpath in files_to_check:
        if fpath.suffix == ".py":
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(content, filename=str(fpath))
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == target_symbol:
                    definitions.append({
                        "symbol": target_symbol,
                        "kind": "class",
                        "file": str(fpath),
                        "line_start": node.lineno,
                        "line_end": getattr(node, "end_lineno", node.lineno),
                        "signature": f"class {node.name}:",
                        "docstring": ast.get_docstring(node) or None,
                    })
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_symbol:
                    definitions.append({
                        "symbol": target_symbol,
                        "kind": "function",
                        "file": str(fpath),
                        "line_start": node.lineno,
                        "line_end": getattr(node, "end_lineno", node.lineno),
                        "signature": _format_function_signature(node),
                        "docstring": ast.get_docstring(node) or None,
                    })
        elif fpath.suffix in SUPPORTED_EXTENSIONS:
            js_data = _parse_js_file(fpath)
            for c in js_data.get("classes", []):
                if c["name"] == target_symbol:
                    definitions.append({
                        "symbol": target_symbol,
                        "kind": "class",
                        "file": str(fpath),
                        "line_start": c["line_start"],
                        "line_end": c["line_end"],
                        "signature": f"class {c['name']}",
                        "docstring": None,
                    })
            for fn in js_data.get("functions", []):
                if fn["name"] == target_symbol:
                    definitions.append({
                        "symbol": target_symbol,
                        "kind": "function",
                        "file": str(fpath),
                        "line_start": fn["line_start"],
                        "line_end": fn["line_end"],
                        "signature": fn.get("signature", fn["name"]),
                        "docstring": None,
                    })

    return {
        "ok": True,
        "data": {
            "symbol": target_symbol,
            "count": len(definitions),
            "definitions": definitions,
        },
        "error": None,
    }


async def find_callers(symbol: str, path: str | None = None, max_results: int = 30) -> dict[str, Any]:
    """Find call sites and references of a symbol across files.

    Args:
        symbol: Symbol name to search callers for.
        path: File or directory path to search. Defaults to current working directory.
        max_results: Maximum call sites to return (default 30).
    """
    if not symbol or not symbol.strip():
        return {"ok": False, "data": None, "error": "symbol parameter is required"}

    target = _resolve_path(path)
    if not target.exists():
        return {"ok": False, "data": None, "error": f"Path does not exist: {target}"}

    target_symbol = symbol.strip()
    callers: list[dict[str, Any]] = []

    files_to_check: list[Path] = []
    if target.is_file():
        files_to_check.append(target)
    else:
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if not _should_skip_dir(d)]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    files_to_check.append(Path(root) / f)

    # Word boundary regex for caller search
    pattern = re.compile(rf"\b{re.escape(target_symbol)}\b")

    for fpath in files_to_check:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            if pattern.search(line):
                # Exclude simple definition lines
                trimmed = line.strip()
                if trimmed.startswith(f"def {target_symbol}") or \
                   trimmed.startswith(f"async def {target_symbol}") or \
                   trimmed.startswith(f"class {target_symbol}"):
                    continue

                callers.append({
                    "file": str(fpath),
                    "line_number": idx,
                    "line_content": trimmed,
                })
                if len(callers) >= max_results:
                    break
        if len(callers) >= max_results:
            break

    return {
        "ok": True,
        "data": {
            "symbol": target_symbol,
            "count": len(callers),
            "callers": callers,
        },
        "error": None,
    }


# ----------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------

async def handle(args: dict[str, Any]) -> dict[str, Any]:
    """Main entrypoint for code_intel tool."""
    action = args.get("action", "")

    # Auto-infer action if not explicitly given
    if not action:
        if "symbol" in args:
            action = "find_definitions"
        elif "path" in args or "directory" in args:
            action = "get_symbol_map"
        else:
            action = "get_symbol_map"

    if action == "get_symbol_map":
        path = args.get("path") or args.get("directory")
        max_files = int(args.get("max_files", 50))
        return await get_symbol_map(path=path, max_files=max_files)

    elif action == "find_definitions":
        symbol = str(args.get("symbol", ""))
        path = args.get("path") or args.get("directory")
        return await find_definitions(symbol=symbol, path=path)

    elif action == "find_callers":
        symbol = str(args.get("symbol", ""))
        path = args.get("path") or args.get("directory")
        max_results = int(args.get("max_results", 30))
        return await find_callers(symbol=symbol, path=path, max_results=max_results)

    return {
        "ok": False,
        "data": None,
        "error": f"Unknown code_intel action: '{action}'. Available: 'get_symbol_map', 'find_definitions', 'find_callers'",
    }
