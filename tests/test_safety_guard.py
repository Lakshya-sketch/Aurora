"""Unit tests for Windows System File Guard and Host Environment Prompt Injection."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from core_logic.prompt_builder import PromptBuilder, get_host_environment_context
from plugins.tools_builtin.handlers.terminal import handle as terminal_handle, validate_system_file_guard


def test_host_environment_context_resolution() -> None:
    """Verify that get_host_environment_context dynamically detects Windows user paths."""
    context = get_host_environment_context()
    assert "[HOST ENVIRONMENT & SHELL RULES]" in context
    assert "Operating System: Windows" in context
    assert "Real Desktop:" in context
    assert "Real Documents:" in context
    assert "PowerShell is the system shell" in context
    assert "CRITICAL SAFETY RULE: You are strictly forbidden from writing, modifying, moving, or deleting any files inside Windows OS directories" in context


def test_prompt_builder_includes_host_rules() -> None:
    """Verify that PromptBuilder injects host environment rules into system prompt."""
    builder = PromptBuilder()
    messages = builder.build_messages("Create a file on desktop")

    assert len(messages) == 2
    sys_content = messages[0]["content"]
    assert "Real Desktop:" in sys_content
    assert "PowerShell is the system shell" in sys_content
    assert "NEVER invent artificial drive root folders like 'E:/Desktop'" in sys_content


@pytest.mark.parametrize(
    "forbidden_cmd",
    [
        'Set-Content -Path "C:\\Windows\\System32\\drivers\\etc\\hosts" -Value "127.0.0.1 bad"',
        'Out-File -FilePath "C:/Windows/test.txt" -InputObject "danger"',
        'Remove-Item -Path "C:\\Windows\\System32\\calc.exe" -Force',
        'del C:\\Windows\\temp\\something.log',
        'echo "hack" > C:\\Windows\\win.ini',
        'echo "bad" >> "C:\\Program Files\\test.exe"',
        'Remove-Item -Path "C:\\Program Files\\Common Files" -Recurse',
        'New-Item -Path "C:\\ProgramData\\Microsoft\\Windows\\evil" -ItemType Directory',
        'takeown /f C:\\Windows\\System32',
        'icacls C:\\Windows /grant Everyone:F',
        'reg add HKLM\\Software\\Microsoft\\Windows /v test /t REG_SZ /d bad',
        'Set-ItemProperty -Path "HKLM:\\SOFTWARE\\Test" -Name "Val" -Value 1',
    ],
)
def test_system_file_guard_blocks_mutations(forbidden_cmd: str) -> None:
    """Verify that all write/delete/mutation operations targeting protected Windows paths are blocked."""
    is_safe, reason = validate_system_file_guard(forbidden_cmd)
    assert is_safe is False
    assert reason is not None
    assert "Access Denied" in reason
    assert "Aurora Windows System File Guard" in reason


@pytest.mark.asyncio
async def test_terminal_handler_hard_blocks_system_mutation() -> None:
    """Verify terminal tool handler rejects protected system writes even when confirmed=True."""
    res = await terminal_handle({
        "command": 'Set-Content -Path "C:\\Windows\\System32\\test.dll" -Value "malicious"',
        "confirmed": True,  # Confirmation must NOT bypass Windows system file guard
    })

    assert res["ok"] is False
    assert res["data"] is None
    assert "Access Denied" in res["error"]
    assert "Aurora Windows System File Guard" in res["error"]


@pytest.mark.parametrize(
    "safe_cmd",
    [
        'Get-ChildItem -Path "C:\\Windows" | Select-Object -First 5',
        'dir "C:\\Program Files"',
        'Get-Content -Path "C:\\Windows\\win.ini" -TotalCount 2',
    ],
)
def test_system_file_guard_allows_safe_reads(safe_cmd: str) -> None:
    """Verify that read-only inspect operations on system directories remain safe and permitted."""
    is_safe, reason = validate_system_file_guard(safe_cmd)
    assert is_safe is True
    assert reason is None


@pytest.mark.asyncio
async def test_terminal_handler_allows_safe_user_command() -> None:
    """Verify normal user operations on desktop / temp locations are permitted."""
    res = await terminal_handle({
        "command": 'Write-Output "Hello from Aurora Guard Test"',
    })

    assert res["ok"] is True
    assert "Hello from Aurora Guard Test" in res["data"]["stdout"]
