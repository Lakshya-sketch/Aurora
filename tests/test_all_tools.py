"""Comprehensive Test Suite & Standalone Runner for All Built-in Tools (Phase 3.0 & 3.1).

Tests all 6 tools in `plugins/tools_builtin/handlers/`:
1. calculator
2. file_search
3. code_exec
4. terminal
5. win_automation
6. browser

Can be run via pytest (`pytest tests/test_all_tools.py -v`) or as a standalone script (`python tests/test_all_tools.py`).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path for direct script execution
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pytest

# Import all tool handlers
from plugins.tools_builtin.handlers.calculator import handle as calc_handler
from plugins.tools_builtin.handlers.file_search import handle as file_search_handler
from plugins.tools_builtin.handlers.code_exec import handle as code_exec_handler
from plugins.tools_builtin.handlers.terminal import handle as terminal_handler
from plugins.tools_builtin.handlers.win_automation import handle as win_auto_handler
from plugins.tools_builtin.handlers.browser import handle as browser_handler
from plugins.tools_builtin.handlers.code_intel import handle as code_intel_handler


# ---------------------------------------------------------------------------
# 1. Calculator Tests
# ---------------------------------------------------------------------------
class TestCalculatorTool:
    """Tests for calculator handler."""

    @pytest.mark.asyncio
    async def test_calculator_basic(self):
        res = await calc_handler({"expression": "15 * 4 + 2"})
        assert res["ok"] is True
        assert res["data"]["result"] == 62

    @pytest.mark.asyncio
    async def test_calculator_sqrt(self):
        res = await calc_handler({"expression": "sqrt(256)"})
        assert res["ok"] is True
        assert res["data"]["result"] == 16


# ---------------------------------------------------------------------------
# 2. File Search Tests
# ---------------------------------------------------------------------------
class TestFileSearchTool:
    """Tests for file_search handler."""

    @pytest.mark.asyncio
    async def test_file_search_local(self, tmp_path: Path):
        test_file = tmp_path / "sample.txt"
        test_file.write_text("Aurora tool test content", encoding="utf-8")

        res = await file_search_handler({
            "directory": str(tmp_path),
            "pattern": "*.txt",
            "read_content": True
        })

        assert res["ok"] is True
        assert res["data"]["count"] >= 1
        assert "Aurora tool test" in res["data"]["matches"][0]["content"]


# ---------------------------------------------------------------------------
# 3. Code Execution Tests
# ---------------------------------------------------------------------------
class TestCodeExecTool:
    """Tests for code_exec handler (Python sandbox)."""

    @pytest.mark.asyncio
    async def test_code_exec_basic(self):
        res = await code_exec_handler({"code": "print('hello from code_exec')" })
        assert res["ok"] is True
        assert "hello from code_exec" in res["data"]["stdout"]
        assert res["data"]["exit_code"] == 0


# ---------------------------------------------------------------------------
# 4. Terminal Tests
# ---------------------------------------------------------------------------
class TestTerminalTool:
    """Tests for terminal handler (PowerShell)."""

    @pytest.mark.asyncio
    async def test_terminal_basic(self):
        res = await terminal_handler({"command": "Write-Output 'aurora_terminal_ok'"})
        assert res["ok"] is True
        assert "aurora_terminal_ok" in res["data"]["stdout"]


# ---------------------------------------------------------------------------
# 5. Win Automation Tests
# ---------------------------------------------------------------------------
class TestWinAutomationTool:
    """Tests for win_automation handler (System info, window list)."""

    @pytest.mark.asyncio
    async def test_system_info(self):
        res = await win_auto_handler({"action": "get_system_info"})
        assert res["ok"] is True
        assert "os" in res["data"]
        assert "cpu" in res["data"]

    @pytest.mark.asyncio
    async def test_get_window_list(self):
        res = await win_auto_handler({"action": "get_window_list"})
        assert res["ok"] is True
        assert isinstance(res["data"]["windows"], list)


# ---------------------------------------------------------------------------
# 6. Browser Tests
# ---------------------------------------------------------------------------
class TestBrowserTool:
    """Tests for browser handler (DuckDuckGo search, HTML page fetch)."""

    @pytest.mark.asyncio
    async def test_browser_search_web(self):
        res = await browser_handler({
            "action": "search_web",
            "query": "Python programming language",
            "max_results": 3
        })
        assert res["ok"] is True
        assert isinstance(res["data"], list)

    @pytest.mark.asyncio
    async def test_browser_get_page_content(self):
        res = await browser_handler({
            "action": "get_page_content",
            "url": "https://example.com"
        })
        assert res["ok"] is True
        assert "Example Domain" in res["data"]


# ---------------------------------------------------------------------------
# 7. Code Intelligence Tests (Phase 4.1 AST / Symbol Graph)
# ---------------------------------------------------------------------------
class TestCodeIntelTool:
    """Tests for code_intel handler (AST symbol map, definitions, callers)."""

    @pytest.mark.asyncio
    async def test_symbol_map(self):
        res = await code_intel_handler({
            "action": "get_symbol_map",
            "path": "core_logic/prompt_builder.py"
        })
        assert res["ok"] is True
        assert res["data"]["language"] == "python"
        assert len(res["data"]["classes"]) >= 1

    @pytest.mark.asyncio
    async def test_find_definitions(self):
        res = await code_intel_handler({
            "action": "find_definitions",
            "symbol": "PromptBuilder",
            "path": "core_logic"
        })
        assert res["ok"] is True
        assert res["data"]["count"] >= 1

    @pytest.mark.asyncio
    async def test_find_callers(self):
        res = await code_intel_handler({
            "action": "find_callers",
            "symbol": "PromptBuilder",
            "path": "core_logic"
        })
        assert res["ok"] is True
        assert res["data"]["count"] >= 1


# ---------------------------------------------------------------------------
# Standalone CLI Runner
# ---------------------------------------------------------------------------
async def run_all_tests():
    """Runs all 7 tool tests sequentially and prints summary report."""
    print("=" * 60)
    print(" [TOOLS] RUNNING ALL BUILT-IN TOOL TESTS (Phase 3.0, 3.1 & 4.1)")
    print("=" * 60)

    results = {}

    # 1. Calculator
    try:
        r1 = await calc_handler({"expression": "100 / 4"})
        results["1. Calculator"] = "PASSED" if r1["ok"] and r1["data"]["result"] == 25 else f"FAILED: {r1.get('error')}"
    except Exception as e:
        results["1. Calculator"] = f"FAILED: {e}"

    # 2. File Search
    try:
        r2 = await file_search_handler({"directory": str(Path.cwd()), "pattern": "AGENTS.md"})
        results["2. File Search"] = "PASSED" if r2["ok"] and r2["data"]["count"] >= 1 else f"FAILED: {r2.get('error')}"
    except Exception as e:
        results["2. File Search"] = f"FAILED: {e}"

    # 3. Code Exec
    try:
        r3 = await code_exec_handler({"code": "import sys; print(f'Python {sys.version_info.major}')"})
        results["3. Code Exec"] = "PASSED" if r3["ok"] and "Python 3" in r3["data"]["stdout"] else f"FAILED: {r3.get('error')}"
    except Exception as e:
        results["3. Code Exec"] = f"FAILED: {e}"

    # 4. Terminal
    try:
        r4 = await terminal_handler({"command": "Get-Date"})
        results["4. Terminal (PowerShell)"] = "PASSED" if r4["ok"] and r4["data"]["exit_code"] == 0 else f"FAILED: {r4.get('error')}"
    except Exception as e:
        results["4. Terminal (PowerShell)"] = f"FAILED: {e}"

    # 5. Win Automation
    try:
        r5 = await win_auto_handler({"action": "get_system_info"})
        results["5. Win Automation"] = "PASSED" if r5["ok"] and "cpu" in r5["data"] else f"FAILED: {r5.get('error')}"
    except Exception as e:
        results["5. Win Automation"] = f"FAILED: {e}"

    # 6. Browser
    try:
        r6 = await browser_handler({"action": "get_page_content", "url": "https://example.com"})
        results["6. Browser"] = "PASSED" if r6["ok"] and "Example Domain" in r6["data"] else f"FAILED: {r6.get('error')}"
    except Exception as e:
        results["6. Browser"] = f"FAILED: {e}"

    # 7. Code Intelligence (AST)
    try:
        r7 = await code_intel_handler({"action": "get_symbol_map", "path": "core_logic/prompt_builder.py"})
        results["7. Code Intelligence"] = "PASSED" if r7["ok"] and r7["data"]["language"] == "python" else f"FAILED: {r7.get('error')}"
    except Exception as e:
        results["7. Code Intelligence"] = f"FAILED: {e}"

    print("\n" + "-" * 60)
    print(" SUMMARY RESULTS:")
    print("-" * 60)
    all_passed = True
    for tool_name, status in results.items():
        icon = "[OK]  " if status == "PASSED" else "[FAIL]"
        print(f" {icon} {tool_name:<25}: {status}")
        if status != "PASSED":
            all_passed = False
    print("=" * 60)

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
