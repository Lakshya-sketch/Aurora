"""Built-in Tools Plugin — Phase 3.0 + 3.1: Core Execution & Desktop Automation Tools.

Subscribes to ``tool.execute`` topic on the EventBus.
Dispatches tool calls to registered handler functions.
Publishes results on ``tool.result`` topic.

Phase 3.0 tools:
  - calculator      : safe math evaluation
  - file_search     : disk file search + content reader
  - code_exec       : Python code execution sandbox
  - terminal        : safe shell command execution (PowerShell/CMD)

Phase 3.1 tools:
  - win_automation  : Windows native OS automation (open apps, list/focus windows)
  - browser         : web content fetching, URL opening, web search
"""

from __future__ import annotations

import logging
from typing import Any

from aurora_core.plugin_base import BasePlugin, PluginContext

from plugins.tools_builtin.handlers.calculator import handle as calculator_handle
from plugins.tools_builtin.handlers.code_exec import handle as code_exec_handle
from plugins.tools_builtin.handlers.file_search import handle as file_search_handle
from plugins.tools_builtin.handlers.terminal import handle as terminal_handle
from plugins.tools_builtin.handlers.win_automation import handle as win_automation_handle
from plugins.tools_builtin.handlers.browser import handle as browser_handle
from plugins.tools_builtin.handlers.code_intel import handle as code_intel_handle
from plugins.tools_builtin.handlers.vision import handle as vision_handle
from plugins.tools_builtin.handlers.image_gen import handle as image_gen_handle

logger = logging.getLogger("aurora.plugin.tools_builtin")

WIN_AUTO_TOOLS = {"open_app", "get_window_list", "focus_window", "get_system_info"}
BROWSER_TOOLS = {"open_url", "get_page_content", "search_web"}
CODE_INTEL_TOOLS = {"get_symbol_map", "find_definitions", "find_callers"}
VISION_TOOLS = {"take_screenshot", "read_screen_text", "inspect_image"}

# Registry: tool_name -> handler coroutine
_HANDLERS: dict[str, Any] = {
    "calculator":      calculator_handle,
    "file_search":     file_search_handle,
    "code_exec":       code_exec_handle,
    "terminal":        terminal_handle,
    "win_automation":  win_automation_handle,
    "browser":         browser_handle,
    "code_intel":      code_intel_handle,
    "vision":          vision_handle,
    # Sub-tool function mappings
    "open_app":        win_automation_handle,
    "get_window_list": win_automation_handle,
    "focus_window":    win_automation_handle,
    "get_system_info": win_automation_handle,
    "open_url":        browser_handle,
    "get_page_content": browser_handle,
    "search_web":      browser_handle,
    "get_symbol_map":  code_intel_handle,
    "find_definitions": code_intel_handle,
    "find_callers":    code_intel_handle,
    "take_screenshot": vision_handle,
    "read_screen_text": vision_handle,
    "inspect_image":   vision_handle,
    "image_gen":       image_gen_handle,
    "generate_image":  image_gen_handle,
}


class ToolsBuiltinPlugin(BasePlugin):
    """Built-in tools plugin: math, file search, code execution, terminal."""

    name = "tools_builtin"
    version = "0.1.0"
    capabilities = ["tools.execute", "tools.catalog"]
    requires: list[str] = []
    subscribes = ["tool.execute", "tools.query", "kernel.ready"]

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None

    async def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        ctx.logger.info(
            "tools_builtin ready: registered tools=%s",
            list(_HANDLERS.keys()),
        )
        # Broadcast tool schemas over EventBus so LLM plugins can discover them without direct imports (Rule 2)
        await ctx.bus.publish("tools.catalog", {"schemas": self.get_tool_schemas()})

    async def shutdown(self) -> None:
        if self._ctx:
            self._ctx.logger.info("tools_builtin shutting down")
        self._ctx = None

    def get_image_model(self) -> str:
        from plugins.tools_builtin.handlers.image_gen import get_active_model
        return get_active_model()

    def set_image_model(self, model_id: str) -> bool:
        from plugins.tools_builtin.handlers.image_gen import set_active_model
        return set_active_model(model_id)

    def get_available_image_models(self) -> list[dict[str, Any]]:
        from plugins.tools_builtin.handlers.image_gen import get_available_models
        return get_available_models()

    async def handle_event(self, topic: str, payload: dict[str, Any]) -> None:
        assert self._ctx is not None

        # Respond to tool schema queries or kernel readiness by advertising catalog
        if topic in ("tools.query", "kernel.ready"):
            await self._ctx.bus.publish("tools.catalog", {"schemas": self.get_tool_schemas()})
            return

        if topic != "tool.execute":
            return

        tool_name  = payload.get("tool", "")
        args       = payload.get("args", {})
        session_id = payload.get("session_id", "default")
        call_id    = payload.get("call_id", "")

        if tool_name not in _HANDLERS:
            # Tool not owned by tools_builtin; allow other plugins (e.g. rag_chroma) to handle it
            return

        # Auto-inject action for sub-tools if missing from args
        if isinstance(args, dict) and (tool_name in WIN_AUTO_TOOLS or tool_name in BROWSER_TOOLS or tool_name in CODE_INTEL_TOOLS or tool_name in VISION_TOOLS):
            if "action" not in args:
                args = dict(args)
                args["action"] = tool_name

        logger.info(
            "tools_builtin executing tool=%s session=%s call_id=%s args=%s",
            tool_name, session_id, call_id, args,
        )

        from aurora_core.tracer import BLUE, trace_step
        trace_step(
            "5/5",
            "Executing Tool",
            "",
            f"Running '{tool_name}' handler",
            f"Args: {args}",
            BLUE,
        )

        try:
            result = await _HANDLERS[tool_name](args)
        except Exception as exc:
            # FIX: catch unhandled handler errors so result is always defined
            logger.error("tools_builtin: tool=%s raised exception: %s", tool_name, exc, exc_info=True)
            result = {"ok": False, "data": None, "error": str(exc)}

        await self._ctx.bus.publish(
            "tool.result",
            {
                "call_id":    call_id,
                "session_id": session_id,
                "tool":       tool_name,
                **result,
            },
        )

    @staticmethod
    def get_tool_schemas() -> list[dict[str, Any]]:
        """Return JSON-schema-compatible tool definitions for LLM function calling."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "calculator",
                    "description": "Evaluate a mathematical expression safely. Supports +, -, *, /, **, %, parentheses, sqrt, log, sin, cos, tan, pi, e.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {
                                "type": "string",
                                "description": "The mathematical expression to evaluate, e.g. '2**10' or '(3+4)*5'",
                            }
                        },
                        "required": ["expression"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_search",
                    "description": "Search for files on disk matching a pattern and optionally read their contents.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directory": {
                                "type": "string",
                                "description": "Directory path to search in. For this codebase, use 'E:/NeuralChat2' or '.' (defaults to active project directory if omitted).",
                            },
                            "pattern": {
                                "type": "string",
                                "description": "Glob pattern to match filenames, e.g. '*.pdf', '*.py', 'resume*'.",
                            },
                            "read_content": {
                                "type": "boolean",
                                "description": "If true, also return text content of matched files (max 4 KB each).",
                            },
                            "recursive": {
                                "type": "boolean",
                                "description": "If true, search subdirectories recursively. Defaults to true.",
                            },
                        },
                        "required": ["directory", "pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "code_exec",
                    "description": "Execute a Python code snippet safely in a sandboxed subprocess. Returns stdout/stderr output.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python code to execute.",
                            },
                            "timeout_s": {
                                "type": "integer",
                                "description": "Maximum execution time in seconds. Defaults to 10.",
                            },
                        },
                        "required": ["code"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "terminal",
                    "description": "Run a shell command (PowerShell on Windows). Returns stdout/stderr.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The shell command to run.",
                            },
                            "cwd": {
                                "type": "string",
                                "description": "Working directory.",
                            },
                            "timeout_s": {
                                "type": "integer",
                                "description": "Timeout in seconds. Defaults to 30.",
                            },
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "open_app",
                    "description": "Open an application by name (e.g. 'notepad', 'calculator', 'chrome') or executable path.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "app_name": {
                                "type": "string",
                                "description": "Application name or executable path.",
                            }
                        },
                        "required": ["app_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_window_list",
                    "description": "List all visible windows on the desktop with their titles and window handles.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "focus_window",
                    "description": "Bring a window to the foreground by matching its title (substring match).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title_contains": {
                                "type": "string",
                                "description": "Substring to match in the window title.",
                            }
                        },
                        "required": ["title_contains"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_system_info",
                    "description": "Get system information: CPU, RAM, OS version, disk usage.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "open_url",
                    "description": "Open a URL in the default system web browser.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "The URL to open.",
                            }
                        },
                        "required": ["url"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_page_content",
                    "description": "Fetch a webpage and extract its readable text content (max 8 KB).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "The URL to fetch.",
                            }
                        },
                        "required": ["url"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search the web using DuckDuckGo and return titles, URLs, and snippets.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query.",
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of results. Defaults to 5.",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_symbol_map",
                    "description": "Inspect code structure of a file or directory using deterministic AST/token parsing. Returns classes, methods, functions, docstrings, and imports for Python/JS/TS without reading entire files.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative or absolute path to a file or directory to inspect (e.g. 'plugins/rag_chroma/document_loader.py' or 'E:/NeuralChat2'). Defaults to active project root if omitted.",
                            },
                            "max_files": {
                                "type": "integer",
                                "description": "Maximum number of files to inspect if path is a directory. Defaults to 50.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_definitions",
                    "description": "Find exact definitions of a symbol (class, function, method) across the codebase with line numbers, file paths, and signatures.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "The symbol name to locate (e.g. 'Orchestrator', 'handle_user_message').",
                            },
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to search within.",
                            },
                        },
                        "required": ["symbol"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_callers",
                    "description": "Find references, instantiations, and call sites of a symbol across the project with line numbers and snippets.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "The symbol name to find callers/usages for.",
                            },
                            "path": {
                                "type": "string",
                                "description": "Optional file or directory path to search within.",
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of caller sites to return. Defaults to 30.",
                            },
                        },
                        "required": ["symbol"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "take_screenshot",
                    "description": "Capture a screenshot of the user's primary or full Windows desktop screen and save to cache.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Optional custom filepath to save the screenshot PNG. Defaults to 'data/.cache/screen.png'.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_screen_text",
                    "description": "Perform OCR on the user's current screen or an image using the local Vision VLM (qwen3-vl). Transcribes visible text, code, titles, and active window contents.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "image_path": {
                                "type": "string",
                                "description": "Optional path to a specific image file to OCR. If omitted, captures and OCRs the current screen.",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "Optional guiding question or focus area for the visual OCR (e.g. 'Transcribe code in the editor').",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_image",
                    "description": "Inspect an image file or screenshot using the Vision Subagent (VLM) to transcribe all text, code, tables, and describe visual layout.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "image_path": {
                                "type": "string",
                                "description": "Path to the image file to inspect (e.g. from user attachments).",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "Optional specific focus or question about the image (e.g. 'Transcribe the code in the screenshot').",
                            },
                        },
                        "required": ["image_path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_image",
                    "description": "Generate an image or illustration from a text prompt. The image is rendered and saved locally, and returned to the user in a visual sub-box.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "Detailed text description of the image to generate.",
                            },
                            "style": {
                                "type": "string",
                                "description": "Visual style, e.g. 'photorealistic', 'anime', 'concept art', 'minimalist', 'oil painting', 'digital art'.",
                            },
                        },
                        "required": ["prompt"],
                    },
                },
            },
        ]


PLUGIN = ToolsBuiltinPlugin
