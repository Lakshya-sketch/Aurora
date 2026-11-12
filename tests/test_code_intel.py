"""Tests for Code Intelligence (AST / Symbol Graph) Handler & Plugin Integration.

Phase 4.1: Tests for get_symbol_map, find_definitions, find_callers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from plugins.tools_builtin.handlers.code_intel import (
    get_symbol_map,
    find_definitions,
    find_callers,
    handle as code_intel_handle,
)
from plugins.tools_builtin.plugin import ToolsBuiltinPlugin
from aurora_core.event_bus import EventBus
from aurora_core.plugin_base import PluginContext


# ----------------------------------------------------------------------
# 1. get_symbol_map Unit Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_symbol_map_python_file():
    """Test AST parsing of a real Python file in the codebase."""
    res = await get_symbol_map(path="core_logic/orchestrator.py")
    assert res["ok"] is True
    data = res["data"]
    assert data["language"] == "python"
    assert len(data["classes"]) >= 1

    orchestrator_cls = next((c for c in data["classes"] if c["name"] == "Orchestrator"), None)
    assert orchestrator_cls is not None
    assert orchestrator_cls["line_start"] > 0
    assert orchestrator_cls["line_end"] >= orchestrator_cls["line_start"]
    assert any(m["name"] == "handle_user_message" for m in orchestrator_cls["methods"])

    # Check imports
    assert len(data["imports"]) > 0


@pytest.mark.asyncio
async def test_get_symbol_map_js_file(tmp_path):
    """Test regex parsing of a JS/JSX file."""
    js_file = tmp_path / "Component.jsx"
    js_file.write_text(
        "import React, { useState } from 'react';\n"
        "import { Button } from './Button';\n"
        "\n"
        "export class Header extends React.Component {}\n"
        "\n"
        "export function MyComponent({ title }) {\n"
        "    return <div>{title}</div>;\n"
        "}\n"
        "\n"
        "const arrowFunc = (a, b) => a + b;\n",
        encoding="utf-8",
    )

    res = await get_symbol_map(path=str(js_file))
    assert res["ok"] is True
    data = res["data"]
    assert data["language"] == "javascript"
    assert len(data["classes"]) == 1
    assert data["classes"][0]["name"] == "Header"
    assert data["classes"][0]["bases"] == ["React.Component"]

    fn_names = [f["name"] for f in data["functions"]]
    assert "MyComponent" in fn_names
    assert "arrowFunc" in fn_names
    assert len(data["imports"]) == 2


@pytest.mark.asyncio
async def test_get_symbol_map_directory():
    """Test directory scanning with get_symbol_map."""
    res = await get_symbol_map(path="core_logic", max_files=10)
    assert res["ok"] is True
    data = res["data"]
    assert data["files_parsed"] >= 1
    assert len(data["symbols"]) >= 1


@pytest.mark.asyncio
async def test_get_symbol_map_nonexistent():
    """Test non-existent path handling."""
    res = await get_symbol_map(path="non/existent/path/never_exists.py")
    assert res["ok"] is False
    assert "does not exist" in res["error"]


@pytest.mark.asyncio
async def test_get_symbol_map_syntax_error(tmp_path):
    """Test graceful handling of syntax error in Python file."""
    bad_py = tmp_path / "bad.py"
    bad_py.write_text("def broken_syntax(:\n    pass", encoding="utf-8")

    res = await get_symbol_map(path=str(bad_py))
    assert res["ok"] is True
    assert "SyntaxError" in res["data"]["error"]


# ----------------------------------------------------------------------
# 2. find_definitions Unit Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_definitions_class():
    """Test locating class definitions across the codebase."""
    res = await find_definitions(symbol="PromptBuilder", path="core_logic")
    assert res["ok"] is True
    data = res["data"]
    assert data["count"] >= 1
    defs = data["definitions"]
    assert any(d["symbol"] == "PromptBuilder" and d["kind"] == "class" for d in defs)


@pytest.mark.asyncio
async def test_find_definitions_function():
    """Test locating function definitions."""
    res = await find_definitions(symbol="build_messages", path="core_logic/prompt_builder.py")
    assert res["ok"] is True
    data = res["data"]
    assert data["count"] >= 1
    assert data["definitions"][0]["kind"] == "function"
    assert "build_messages" in data["definitions"][0]["signature"]


@pytest.mark.asyncio
async def test_find_definitions_empty_symbol():
    """Test validation of symbol parameter."""
    res = await find_definitions(symbol="")
    assert res["ok"] is False
    assert "required" in res["error"]


@pytest.mark.asyncio
async def test_find_definitions_nonexistent_symbol():
    """Test searching for a symbol that does not exist."""
    res = await find_definitions(symbol="NonExistentClassSymbol12345", path="core_logic")
    assert res["ok"] is True
    assert res["data"]["count"] == 0


# ----------------------------------------------------------------------
# 3. find_callers Unit Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_callers():
    """Test locating callers / references of a symbol."""
    res = await find_callers(symbol="PromptBuilder", path="core_logic")
    assert res["ok"] is True
    data = res["data"]
    assert data["count"] >= 1
    assert any("PromptBuilder" in c["line_content"] for c in data["callers"])


@pytest.mark.asyncio
async def test_find_callers_empty_symbol():
    """Test validation of symbol in find_callers."""
    res = await find_callers(symbol="")
    assert res["ok"] is False
    assert "required" in res["error"]


# ----------------------------------------------------------------------
# 4. Handler Dispatcher & Sub-tool Mapping Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_code_intel_dispatcher():
    """Test direct calls to the code_intel handle dispatcher."""
    # Test action=get_symbol_map
    res1 = await code_intel_handle({"action": "get_symbol_map", "path": "core_logic/prompt_builder.py"})
    assert res1["ok"] is True

    # Test auto-infer action from symbol
    res2 = await code_intel_handle({"symbol": "PromptBuilder", "path": "core_logic"})
    assert res2["ok"] is True
    assert res2["data"]["symbol"] == "PromptBuilder"

    # Test action=find_callers
    res3 = await code_intel_handle({"action": "find_callers", "symbol": "PromptBuilder", "path": "core_logic"})
    assert res3["ok"] is True


# ----------------------------------------------------------------------
# 5. ToolsBuiltinPlugin EventBus Integration Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_builtin_code_intel_eventbus():
    """Verify that ToolsBuiltinPlugin executes get_symbol_map and find_definitions over EventBus."""
    bus = EventBus()
    plugin = ToolsBuiltinPlugin()
    ctx = PluginContext(name=plugin.name, config={}, bus=bus)
    await plugin.setup(ctx)

    await bus.subscribe("tool.execute", plugin.handle_event)

    results = []

    async def on_result(topic: str, payload: dict):
        results.append(payload)

    await bus.subscribe("tool.result", on_result)

    # 1. Test get_symbol_map tool execution
    await bus.publish(
        "tool.execute",
        {
            "call_id": "call-ast-1",
            "session_id": "test-session",
            "tool": "get_symbol_map",
            "args": {"path": "core_logic/prompt_builder.py"},
        },
    )

    await asyncio.sleep(0.05)
    assert len(results) == 1
    r1 = results[0]
    assert r1["call_id"] == "call-ast-1"
    assert r1["ok"] is True
    assert r1["data"]["language"] == "python"

    # 2. Test find_definitions tool execution
    await bus.publish(
        "tool.execute",
        {
            "call_id": "call-ast-2",
            "session_id": "test-session",
            "tool": "find_definitions",
            "args": {"symbol": "PromptBuilder", "path": "core_logic"},
        },
    )

    await asyncio.sleep(0.05)
    assert len(results) == 2
    r2 = results[1]
    assert r2["call_id"] == "call-ast-2"
    assert r2["ok"] is True
    assert r2["data"]["count"] >= 1

    await plugin.shutdown()
