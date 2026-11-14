"""Prompt Builder — assembles system instructions and chat messages for LLM completion.

Conforms to standard chat completions schema:
  [{"role": "system", "content": ...}, {"role": "user", "content": ...}]
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def get_host_environment_context() -> str:
    """Detect dynamic Windows host paths and environment rules."""
    userprofile_str = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    userprofile = Path(userprofile_str)

    # Detect Desktop (OneDrive redirected or standard)
    onedrive_desktop = userprofile / "OneDrive" / "Desktop"
    desktop = onedrive_desktop if onedrive_desktop.exists() else userprofile / "Desktop"

    # Detect Documents (OneDrive redirected or standard)
    onedrive_docs = userprofile / "OneDrive" / "Documents"
    docs = onedrive_docs if onedrive_docs.exists() else userprofile / "Documents"

    downloads = userprofile / "Downloads"

    return (
        "\n\n[HOST ENVIRONMENT & SHELL RULES]\n"
        "- Operating System: Windows\n"
        f"- User Home: {userprofile.as_posix()}\n"
        f"- Real Desktop: {desktop.as_posix()}\n"
        f"- Real Documents: {docs.as_posix()}\n"
        f"- Real Downloads: {downloads.as_posix()}\n"
        "- Terminal Shell: PowerShell is the system shell. Use PowerShell cmdlets (e.g. Set-Content, Out-File, Get-ChildItem). "
        "Do NOT use Command Prompt (cmd.exe) syntax like %HOMEDRIVE%, %USERPROFILE%, or &&. "
        "When the user asks to save, create, or read files on their 'Desktop' or 'Documents', ALWAYS use the exact Real Desktop or Documents paths listed above. "
        "NEVER invent artificial drive root folders like 'E:/Desktop' or 'E:/Users/Public/Desktop'.\n"
        "- CRITICAL SAFETY RULE: You are strictly forbidden from writing, modifying, moving, or deleting any files inside Windows OS directories "
        "(such as C:/Windows, System32, Program Files, or Registry). Any attempt to modify system files will be rejected by the Aurora safety guard."
    )


BASE_SYSTEM_INSTRUCTIONS = (
    "You are Aurora, an intelligent local AI assistant running on the user's Windows PC. "
    "You HAVE local system tools available: "
    "rag_search (search through uploaded user documents, code, PDFs, notes, and manuals using semantic vector similarity), "
    "file_search (search for file paths on the hard drive), calculator (evaluate math), "
    "code_exec (execute Python code), terminal (run shell commands), open_app (launch applications), "
    "get_window_list, focus_window, get_system_info, open_url, get_page_content, search_web, "
    "take_screenshot (capture the user's desktop screen), read_screen_text (perform visual OCR to transcribe text from screen), "
    "inspect_image (inspect attached images/photos using the Vision Subagent to transcribe code, read text, or describe layout), "
    "get_symbol_map (inspect code AST/structure), find_definitions (locate classes/functions), and find_callers (find usages). "
    "When answering questions about documents, code, or files the user has shared or uploaded, rely on the injected reference excerpts, attached files, or call rag_search. "
    "MANDATORY VISION DIRECTIVE: You cannot see raw image pixels directly. Whenever an image is attached or the user references an image or screenshot ('in this code', 'why is this highlighted', 'look at this', etc.), you MUST call the `inspect_image` tool to read, OCR, and analyze the image before providing your answer. Never guess or provide generic answers without inspecting the image first. "
    "Only search this assistant's own codebase (E:/NeuralChat2) if the user explicitly asks about Aurora's internal codebase or implementation. Do NOT confuse Aurora's internal code with user-attached files or user projects. "
    "When the user asks to find/search files on disk, inspect code structure, open apps, do calculations, or perform system tasks, "
    "you MUST call the appropriate tool. NEVER claim that you lack access to local files or tools."
)

DEFAULT_SYSTEM_PROMPT = BASE_SYSTEM_INSTRUCTIONS + get_host_environment_context()


class PromptBuilder:
    """Assembles chat prompts for LLM plugins."""

    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        self.system_prompt = system_prompt

    def build_messages(
        self,
        user_text: str,
        system_override: str | None = None,
        history: list[dict] | None = None,
        context: str | None = None,
        images: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Construct a list of chat message objects with optional context injection.

        Args:
            user_text: The latest user prompt.
            system_override: Optional custom system prompt overriding the default.
            history: Optional list of past message dicts `[{"role": "...", "content": "..."}]`.
            context: Optional reference context (e.g. RAG retrieved document chunks, AST symbols)
                     injected cleanly into the system instructions.
            images: Optional list of base64-encoded image strings attached to the prompt.

        Returns:
            List of message dicts formatted for chat API.
        """
        sys_content = system_override or self.system_prompt
        if context:
            sys_content += (
                f"\n\n=== CONTEXT / REFERENCE KNOWLEDGE ===\n"
                f"{context}\n"
                f"=== END CONTEXT ===\n"
                f"Answer the user's prompt using the reference context above when relevant. "
                f"Cite specific sections, pages, or symbols when applicable."
            )

        if "ATTACHED IMAGE FILE:" in (context or "") or images:
            sys_content += (
                "\n\n[MANDATORY ATTACHED IMAGE DIRECTIVE]\n"
                "The user has attached an image or screenshot to this request. "
                "Because you cannot see image pixels directly, you MUST invoke the `inspect_image` tool "
                "to transcribe and inspect the visual content, code, and styling before answering. "
                "Do NOT guess or answer with generic assumptions without inspecting the image first."
            )

        messages: list[dict[str, Any]] = [{"role": "system", "content": sys_content}]

        if history:
            for msg in history:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    m_entry = {"role": str(msg["role"]), "content": str(msg["content"])}
                    if "images" in msg:
                        m_entry["images"] = msg["images"]
                    messages.append(m_entry)

        user_entry: dict[str, Any] = {"role": "user", "content": user_text}
        if images:
            user_entry["images"] = images
        messages.append(user_entry)
        return messages
