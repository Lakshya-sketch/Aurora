"""Tests for Phase 3.0 — Built-in Tools Plugin (calculator, file_search, code_exec, terminal).

Per AGENTS.md Rule 10: gate must be green before Phase 3.1 starts.

All tests are async (pytest-asyncio auto mode configured in pytest.ini).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Calculator tests
# ---------------------------------------------------------------------------
from plugins.tools_builtin.handlers.calculator import handle as calc


class TestCalculator:
    """Tests for the safe-eval calculator handler."""

    @pytest.mark.asyncio
    async def test_basic_addition(self):
        result = await calc({"expression": "2 + 3"})
        assert result["ok"] is True
        assert result["data"]["result"] == 5

    @pytest.mark.asyncio
    async def test_power_operator_double_star(self):
        result = await calc({"expression": "2**10"})
        assert result["ok"] is True
        assert result["data"]["result"] == 1024

    @pytest.mark.asyncio
    async def test_power_operator_caret(self):
        """Caret notation (2^10) must be normalised to 2**10."""
        result = await calc({"expression": "2^10"})
        assert result["ok"] is True
        assert result["data"]["result"] == 1024

    @pytest.mark.asyncio
    async def test_float_result(self):
        result = await calc({"expression": "10 / 3"})
        assert result["ok"] is True
        assert abs(result["data"]["result"] - 3.3333333333) < 1e-6

    @pytest.mark.asyncio
    async def test_builtin_sqrt(self):
        result = await calc({"expression": "sqrt(144)"})
        assert result["ok"] is True
        assert result["data"]["result"] == 12

    @pytest.mark.asyncio
    async def test_builtin_pi(self):
        result = await calc({"expression": "round(pi, 4)"})
        assert result["ok"] is True
        assert result["data"]["result"] == 3.1416

    @pytest.mark.asyncio
    async def test_complex_expression(self):
        result = await calc({"expression": "(3 + 4) * 5 - 2"})
        assert result["ok"] is True
        assert result["data"]["result"] == 33

    @pytest.mark.asyncio
    async def test_division_by_zero(self):
        result = await calc({"expression": "1 / 0"})
        assert result["ok"] is False
        assert "zero" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_disallowed_import(self):
        """__import__ and similar should be rejected."""
        result = await calc({"expression": "__import__('os')"})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_empty_expression(self):
        result = await calc({"expression": ""})
        assert result["ok"] is False
        assert result["error"]

    @pytest.mark.asyncio
    async def test_expression_preserved_in_data(self):
        result = await calc({"expression": "6 * 7"})
        assert result["ok"] is True
        assert result["data"]["expression"] == "6 * 7"


# ---------------------------------------------------------------------------
# File search tests
# ---------------------------------------------------------------------------
from plugins.tools_builtin.handlers.file_search import handle as file_search


class TestFileSearch:
    """Tests for the file search + content reader handler."""

    @pytest.mark.asyncio
    async def test_basic_search(self, tmp_path):
        (tmp_path / "hello.txt").write_text("hello world")
        (tmp_path / "other.md").write_text("# heading")
        result = await file_search({"directory": str(tmp_path), "pattern": "*.txt"})
        assert result["ok"] is True
        assert result["data"]["count"] == 1
        assert result["data"]["matches"][0]["path"].endswith("hello.txt")

    @pytest.mark.asyncio
    async def test_no_matches(self, tmp_path):
        result = await file_search({"directory": str(tmp_path), "pattern": "*.xyz"})
        assert result["ok"] is True
        assert result["data"]["count"] == 0

    @pytest.mark.asyncio
    async def test_read_content(self, tmp_path):
        (tmp_path / "data.txt").write_text("Aurora v2")
        result = await file_search(
            {"directory": str(tmp_path), "pattern": "*.txt", "read_content": True}
        )
        assert result["ok"] is True
        assert result["data"]["matches"][0]["content"] == "Aurora v2"

    @pytest.mark.asyncio
    async def test_recursive_search(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("x = 1")
        result = await file_search(
            {"directory": str(tmp_path), "pattern": "*.py", "recursive": True}
        )
        assert result["ok"] is True
        assert result["data"]["count"] == 1

    @pytest.mark.asyncio
    async def test_nonexistent_directory(self):
        result = await file_search({"directory": "/no/such/dir", "pattern": "*.txt"})
        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_missing_directory_arg(self):
        result = await file_search({"pattern": "*.txt"})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_size_bytes_present(self, tmp_path):
        (tmp_path / "a.txt").write_text("1234567890")
        result = await file_search({"directory": str(tmp_path), "pattern": "*.txt"})
        assert result["ok"] is True
        assert result["data"]["matches"][0]["size_bytes"] == 10


# ---------------------------------------------------------------------------
# Code execution tests
# ---------------------------------------------------------------------------
from plugins.tools_builtin.handlers.code_exec import handle as code_exec


class TestCodeExec:
    """Tests for the Python sandbox code execution handler."""

    @pytest.mark.asyncio
    async def test_hello_world(self):
        result = await code_exec({"code": "print('hello')"})
        assert result["ok"] is True
        assert "hello" in result["data"]["stdout"]
        assert result["data"]["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_math_output(self):
        result = await code_exec({"code": "print(2 ** 10)"})
        assert result["ok"] is True
        assert "1024" in result["data"]["stdout"]

    @pytest.mark.asyncio
    async def test_stderr_captured(self):
        result = await code_exec({"code": "import sys; sys.stderr.write('err')"})
        assert result["ok"] is True
        assert "err" in result["data"]["stderr"]

    @pytest.mark.asyncio
    async def test_exit_code_nonzero(self):
        result = await code_exec({"code": "import sys; sys.exit(42)"})
        assert result["ok"] is True
        assert result["data"]["exit_code"] == 42

    @pytest.mark.asyncio
    async def test_syntax_error_captured(self):
        result = await code_exec({"code": "def foo("})
        assert result["ok"] is True
        assert result["data"]["exit_code"] != 0
        assert result["data"]["stderr"]  # SyntaxError in stderr

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        result = await code_exec({"code": "import time; time.sleep(99)", "timeout_s": 2})
        assert result["ok"] is True
        assert result["data"]["timed_out"] is True

    @pytest.mark.asyncio
    async def test_empty_code(self):
        result = await code_exec({"code": ""})
        assert result["ok"] is False
        assert result["error"]


# ---------------------------------------------------------------------------
# Terminal tests
# ---------------------------------------------------------------------------
from plugins.tools_builtin.handlers.terminal import handle as terminal


class TestTerminal:
    """Tests for the PowerShell terminal handler."""

    @pytest.mark.asyncio
    async def test_echo_command(self):
        result = await terminal({"command": "echo hello_aurora"})
        assert result["ok"] is True
        assert "hello_aurora" in result["data"]["stdout"]

    @pytest.mark.asyncio
    async def test_exit_code_zero(self):
        result = await terminal({"command": "echo ok"})
        assert result["ok"] is True
        assert result["data"]["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_command_stored_in_data(self):
        result = await terminal({"command": "echo test"})
        assert result["ok"] is True
        assert result["data"]["command"] == "echo test"

    @pytest.mark.asyncio
    async def test_dangerous_command_blocked(self):
        result = await terminal({"command": "rm -rf /"})
        assert result["ok"] is False
        assert "dangerous" in result["error"].lower() or "confirmation" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_dangerous_command_allowed_with_confirmed(self):
        """A dangerous-pattern command is allowed when confirmed=True.
        We use a safe-to-run command that matches no real harm here.
        We just test the bypass gate, not actual rm."""
        # Use a pattern that triggers blocklist but is actually harmless
        result = await terminal(
            {"command": "echo 'rm -rf is just text'", "confirmed": True}
        )
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_empty_command(self):
        result = await terminal({"command": ""})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        result = await terminal({"command": "Start-Sleep -Seconds 99", "timeout_s": 2})
        assert result["ok"] is True
        assert result["data"]["timed_out"] is True
