"""Unit tests for PromptBuilder."""

from __future__ import annotations

from core_logic.prompt_builder import DEFAULT_SYSTEM_PROMPT, PromptBuilder


def test_prompt_builder_default() -> None:
    builder = PromptBuilder()
    messages = builder.build_messages("Hello")

    assert len(messages) == 2
    assert messages[0] == {"role": "system", "content": DEFAULT_SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": "Hello"}


def test_prompt_builder_system_override() -> None:
    builder = PromptBuilder()
    custom_sys = "You are a coding assistant."
    messages = builder.build_messages("Fix this bug", system_override=custom_sys)

    assert len(messages) == 2
    assert messages[0]["content"] == custom_sys
    assert messages[1]["content"] == "Fix this bug"


def test_prompt_builder_history() -> None:
    builder = PromptBuilder()
    history = [
        {"role": "user", "content": "What is 2+2?"},
        {"role": "assistant", "content": "4"},
    ]
    messages = builder.build_messages("What about 3+3?", history=history)

    assert len(messages) == 4
    assert messages[1] == {"role": "user", "content": "What is 2+2?"}
    assert messages[2] == {"role": "assistant", "content": "4"}
    assert messages[3] == {"role": "user", "content": "What about 3+3?"}
