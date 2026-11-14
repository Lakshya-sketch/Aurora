"""Ollama LLM Plugin — real local LLM streaming via Ollama API.

Subscribes to ``llm.generate`` and ``user.message``.
Connects to Ollama `/api/chat`, streams tokens over ``llm.token``, and completes with ``chat.complete``.
Auto-detects installed Ollama models and logs API calls live to terminal.

Phase 3.1: Supports LLM function calling (tool-use loop).
When tool schemas are provided, Ollama may return tool_calls instead of text.
The plugin executes tools via EventBus ``tool.execute`` / ``tool.result`` and feeds
the tool output back to the LLM for a final answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from typing import Any

import httpx

from aurora_core.plugin_base import BasePlugin, PluginContext

logger = logging.getLogger("aurora.plugin.llm_ollama")

# Maximum tool-call rounds to prevent infinite loops
MAX_TOOL_ROUNDS = 5


class OllamaPlugin(BasePlugin):
    """Local LLM plugin wrapping Ollama chat API with streaming support."""

    name = "llm_ollama"
    version = "0.2.0"
    capabilities = ["llm.chat"]
    requires: list[str] = []
    subscribes = ["llm.generate", "tool.result", "tools.catalog"]

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self.base_url: str = "http://127.0.0.1:11434"
        self.default_model: str = "qwen2.5:3b-instruct-q4_K_M"
        self.vlm_model: str = "qwen3-vl:4b-instruct-q4_K_M"
        self._selected_model: str | None = None
        self._selected_vlm_model: str | None = None
        self.active_model: str = self.get_selected_model()
        self.timeout_s: float = 120.0
        self.total_tokens_spent: int = 0
        self.num_gpu: int = 99
        self.num_ctx: int = 4096
        self.installed_models: list[str] = []
        self._tool_schemas: list[dict[str, Any]] = []
        # Pending tool-call futures: call_id -> asyncio.Future
        self._tool_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def get_selected_model(self) -> str:
        """Return the currently selected/active model."""
        return getattr(self, "_selected_model", None) or getattr(self, "active_model", None) or self.default_model

    async def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        llm_cfg = ctx.config.get("llm", {})
        self.base_url = str(llm_cfg.get("base_url", "http://127.0.0.1:11434")).rstrip("/")
        self.default_model = str(llm_cfg.get("default_model", "qwen2.5:3b-instruct-q4_K_M"))
        self.vlm_model = str(llm_cfg.get("vlm_model", "qwen3-vl:4b-instruct-q4_K_M"))
        self.timeout_s = float(llm_cfg.get("timeout_s", 120.0))
        self.num_gpu = int(llm_cfg.get("num_gpu", 99))
        self.num_ctx = int(llm_cfg.get("num_ctx", 4096))

        # Check installed models on setup once
        self.installed_models = await self._get_installed_models()
        usable = [m for m in self.installed_models if any(k in m for k in ["qwen", "gemma", "llama", "mistral", "phi"])]
        if not usable and self.installed_models:
            usable = self.installed_models

        if usable:
            if self.default_model in usable:
                self._selected_model = self.default_model
            else:
                self._selected_model = usable[0]
        else:
            self._selected_model = self.default_model

        self.active_model = self._selected_model

        # Ensure VLM model is configured or matched from installed models
        if self.vlm_model not in self.installed_models and self.installed_models:
            vlm_candidates = [m for m in self.installed_models if "vl" in m.lower() or "vision" in m.lower()]
            if vlm_candidates:
                self.vlm_model = vlm_candidates[0]
        self._selected_vlm_model = self.vlm_model

        sys.stdout.write(f"\n[llm_ollama] Plugin ready | base_url={self.base_url} | selected_model={self.active_model} | vlm_model={self.vlm_model}\n")
        sys.stdout.flush()

        ctx.logger.info(
            "llm_ollama ready: base_url=%s, active_model=%s, installed_models=%s",
            self.base_url,
            self.active_model,
            self.installed_models,
        )

        # Query available tool schemas over the EventBus (Rule 2)
        await ctx.bus.publish("tools.query", {})

    async def shutdown(self) -> None:
        if self._ctx:
            self._ctx.logger.info("llm_ollama shutting down")
        self._ctx = None

    async def _fetch_models_from_ollama(self) -> tuple[bool, list[str]]:
        """Fetch live models from Ollama /api/tags.

        Returns:
            (is_online: bool, model_names: list[str])
        """
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                res = await client.get(f"{self.base_url}/api/tags")
                if res.status_code == 200:
                    data = res.json()
                    models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
                    return True, models
        except Exception as exc:
            logger.debug("Ollama /api/tags unreachable: %s", exc)
        return False, []

    async def refresh_installed_models(self) -> list[str]:
        """Fetch current live models from Ollama and dynamically sync active model."""
        online, live_models = await self._fetch_models_from_ollama()
        if online:
            self.installed_models = live_models
            usable = [m for m in self.installed_models if any(k in m for k in ["qwen", "gemma", "llama", "mistral", "phi"])]
            if not usable and self.installed_models:
                usable = self.installed_models

            if not self.installed_models:
                self._selected_model = None
                self.active_model = "None (No models installed)"
            elif self.active_model not in self.installed_models and usable:
                # Active model was deleted or changed in Ollama — auto-fallback
                matched = [m for m in usable if any(part in m for part in self.active_model.split(":")[:1])]
                self._selected_model = matched[0] if matched else usable[0]
                self.active_model = self._selected_model
                if self._ctx:
                    await self._ctx.bus.publish(
                        "stats.update",
                        {
                            "active_model": self.active_model,
                            "total_tokens": self.total_tokens_spent,
                            "installed_models": self.installed_models,
                        },
                    )
        return self.installed_models

    async def _get_installed_models(self) -> list[str]:
        _, models = await self._fetch_models_from_ollama()
        return models

    # ------------------------------------------------------------------
    # Tool schemas — cached via EventBus from tools.catalog event (Rule 2)
    # ------------------------------------------------------------------

    def _get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return tool schemas received via EventBus (tools.catalog)."""
        return list(self._tool_schemas)

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    async def handle_event(self, topic: str, payload: dict[str, Any]) -> None:
        if topic == "tools.catalog":
            schemas = payload.get("schemas", [])
            if isinstance(schemas, list):
                self._tool_schemas = schemas
                if self._ctx:
                    self._ctx.logger.debug("llm_ollama received %d tool schemas via tools.catalog", len(schemas))
            return

        if topic == "tool.result":
            # Resolve a pending tool-call future
            call_id = payload.get("call_id", "")
            future = self._tool_futures.get(call_id)
            if future and not future.done():
                future.set_result(payload)
            return

        if topic not in ("llm.generate", "user.message"):
            return
        assert self._ctx is not None

        session_id = payload.get("session_id", "default")
        requested_model = payload.get("model") or self.get_selected_model()

        # Extract or build messages
        messages = payload.get("messages")
        if not messages and "text" in payload:
            text = payload.get("text", "")
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are Aurora, an intelligent local AI assistant running on the user's Windows PC. "
                        "The active project root for this codebase is E:/NeuralChat2. When searching for files, reading code, "
                        "or inspecting this codebase, always use 'E:/NeuralChat2' (or relative paths like 'plugins/rag_chroma') as the directory or path. "
                        "You HAVE local system tools available: "
                        "rag_search (search through uploaded documents, PDFs, notes, and manuals using semantic vector similarity), "
                        "file_search (search for file paths on the hard drive), calculator (evaluate math), "
                        "code_exec (execute Python code), terminal (run shell commands), open_app (launch applications), "
                        "get_window_list, focus_window, get_system_info, open_url, get_page_content, search_web, "
                        "get_symbol_map (inspect code AST/structure), find_definitions (locate classes/functions), and find_callers (find usages). "
                        "When answering questions about documents or PDFs the user has shared, rely first on the injected reference excerpts or call rag_search. "
                        "When the user asks to find/search files on disk, inspect code structure, open apps, do calculations, or perform system tasks, "
                        "you MUST call the appropriate tool. NEVER claim that you lack access to local files or tools."
                    ),
                },
                {"role": "user", "content": text},
            ]

        if not messages:
            logger.warning("llm_ollama: received empty messages payload for session=%s", session_id)
            return

        # If payload has top-level images but user message doesn't, attach them to the last user message
        if payload.get("images") and messages:
            for m in reversed(messages):
                if m.get("role") == "user":
                    m["images"] = list(payload.get("images"))
                    break

        # Refresh live models from Ollama to ensure deleted models are cleared
        online, live_models = await self._fetch_models_from_ollama()
        if online:
            self.installed_models = live_models
            if not self.installed_models:
                error_msg = (
                    "⚠️ No Ollama models are installed. Please run `ollama pull qwen2.5:3b-instruct-q4_K_M` "
                    "(or any model of your choice) in your terminal."
                )
                await self._emit_fallback(session_id, error_msg)
                return

            usable = [m for m in self.installed_models if any(k in m for k in ["qwen", "gemma", "llama", "mistral", "phi"])]
            if not usable and self.installed_models:
                usable = self.installed_models

            # Model selection: use installed models if needed
            target_model = requested_model
            if usable and target_model not in self.installed_models:
                matched = [m for m in usable if any(part in m for part in target_model.split(":")[:1])]
                target_model = matched[0] if matched else usable[0]

            self._selected_model = target_model
            self.active_model = target_model
        else:
            # Ollama is offline or unreachable - keep current target model so _call_ollama handles connection error
            target_model = requested_model or self.get_selected_model()
            self._selected_model = target_model
            self.active_model = target_model

        # Terminal output via stderr so Electron shows it as [py:err]
        prompt_text = messages[-1].get("content", "") if messages else ""
        system_content = messages[0].get("content", "") if messages else ""

        context_traceback_lines: list[str] = []
        sections: list[tuple[str, list[str]]] = []

        if "=== CONTEXT / REFERENCE KNOWLEDGE ===" in system_content:
            raw_context = system_content.split("=== CONTEXT / REFERENCE KNOWLEDGE ===")[1].split("=== END CONTEXT ===")[0].strip()
            context_traceback_lines.append(f"  Context: {len(raw_context):,} chars injected into system instructions")
            context_traceback_lines.append("  -----------------------------------------------------")

            curr_header = None
            curr_content: list[str] = []
            for line in raw_context.splitlines():
                s = line.strip()
                if not s or s.startswith("=== END"):
                    continue
                if s.startswith(("[", "===")):
                    if curr_header:
                        sections.append((curr_header, curr_content))
                    curr_header = s
                    curr_content = []
                else:
                    curr_content.append(s)
            if curr_header:
                sections.append((curr_header, curr_content))

        # Check messages for injected vision subagent tool outputs (OCR / image descriptions)
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "tool":
                try:
                    c_raw = m.get("content", "")
                    c_dict = json.loads(c_raw) if isinstance(c_raw, str) else c_raw
                    t_data = c_dict.get("data", {}) if isinstance(c_dict, dict) else {}
                    if isinstance(t_data, dict):
                        if "description_and_ocr" in t_data:
                            desc = str(t_data.get("description_and_ocr", "")).strip()
                            fname = t_data.get("filename") or "attached image"
                            model_used = t_data.get("model_used")
                            model_tag = f" ({model_used})" if model_used else ""
                            v_hdr = f"=== VISION SUBAGENT OCR & CONTEXT: {fname}{model_tag} ==="
                            sections.append((v_hdr, [desc]))
                        elif "screen_text" in t_data:
                            desc = str(t_data.get("screen_text", "")).strip()
                            model_used = t_data.get("model_used")
                            model_tag = f" ({model_used})" if model_used else ""
                            v_hdr = f"=== VISION SCREEN OCR CONTEXT{model_tag} ==="
                            sections.append((v_hdr, [desc]))
                        elif "image_path" in t_data and "prompt" in t_data:
                            desc = str(t_data.get("description", t_data.get("prompt", ""))).strip()
                            fname = t_data.get("filename") or "generated_image"
                            model_used = t_data.get("model_used")
                            model_tag = f" ({model_used})" if model_used else ""
                            v_hdr = f"=== GENERATED IMAGE RESULT: {fname}{model_tag} ==="
                            sections.append((v_hdr, [desc]))
                except Exception:
                    pass

        if sections:
            context_traceback_lines.append("  [CONTEXT TRACEBACK] Injected Reference Knowledge & Vision Context:")
            max_show = 14
            items_to_show = sections[:max_show]
            total_items = len(sections)

            for idx, (h, contents) in enumerate(items_to_show):
                is_last = (idx == len(items_to_show) - 1) and (total_items <= max_show)
                branch = "    └─ " if is_last else "    ├─ "
                context_traceback_lines.append(f"{branch}{h}")

                first_line = None
                for c in contents:
                    c_clean = c.strip()
                    if not c_clean:
                        continue
                    if c_clean.startswith("File:") or c_clean.startswith("Size:"):
                        continue
                    first_line = c_clean
                    break
                if not first_line and contents:
                    first_line = contents[0].strip()

                is_banner = (
                    h.startswith("=== LONG-TERM MEMORY")
                    or h.startswith("=== RELEVANT DOCUMENT EXCERPTS")
                    or h.startswith("=== RETRIEVED")
                )
                if first_line and not is_banner:
                    pipe = "       " if is_last else "    │  "
                    clean_first = first_line.replace("\n", " ").strip()
                    preview = (clean_first[:117] + "...") if len(clean_first) > 120 else clean_first
                    context_traceback_lines.append(f'{pipe}└─ "{preview}"')

            if total_items > max_show:
                context_traceback_lines.append(f"    └─ ... (+{total_items - max_show} additional sections active in prompt context)")
        elif "=== CONTEXT / REFERENCE KNOWLEDGE ===" in system_content:
            preview = raw_context[:160].replace("\n", " ")
            context_traceback_lines.append(f"    └─ Preview: \"{preview}...\"")

        log_parts = [
            "\n=======================================================",
            f"[API CALL] POST {self.base_url}/api/chat",
            f"  Model:   {target_model}",
            f"  Session: {session_id}",
            f"  Prompt:  {prompt_text!r}",
        ]
        if context_traceback_lines:
            log_parts.extend(context_traceback_lines)
        log_parts.append("=======================================================\n")
        log_line = "\n".join(log_parts)

        try:
            sys.stderr.write(log_line)
        except UnicodeEncodeError:
            sys.stderr.write(log_line.encode("ascii", errors="replace").decode("ascii"))
        sys.stderr.flush()

        self._ctx.logger.info("llm_ollama streaming generation for session=%s, model=%s", session_id, target_model)

        # Get tool schemas for function calling
        tool_schemas = self._get_tool_schemas()

        from aurora_core.tracer import YELLOW, trace_step
        trace_step(
            "3/5",
            "Ollama API",
            "",
            f"Invoking Ollama API (Model: {target_model})",
            f"Attached {len(tool_schemas)} tool schemas | Session: {session_id}",
            YELLOW,
        )

        await self._generation_loop(session_id, target_model, messages, tool_schemas)

    # ------------------------------------------------------------------
    # Generation loop with tool-call support
    # ------------------------------------------------------------------

    async def _generation_loop(
        self,
        session_id: str,
        model: str,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> None:
        """Run the LLM generation, handling tool calls in a loop.

        If the LLM returns tool_calls, we execute them, feed the results
        back as 'tool' role messages, and re-invoke the LLM. This repeats
        for up to MAX_TOOL_ROUNDS to prevent infinite loops.
        """
        assert self._ctx is not None

        for round_num in range(MAX_TOOL_ROUNDS + 1):
            result = await self._call_ollama(session_id, model, messages, tool_schemas)

            if result is None:
                # Error already handled inside _call_ollama
                return

            tool_calls = result.get("tool_calls")

            if not tool_calls:
                # Normal text response — we already streamed it. Done!
                break

            # ── Tool calls detected — execute them ──────────────
            if round_num >= MAX_TOOL_ROUNDS:
                logger.warning("llm_ollama: hit max tool rounds (%d), stopping", MAX_TOOL_ROUNDS)
                await self._emit_fallback(session_id, "⚠️ Reached maximum tool call rounds. Please try a simpler request.")
                return

            from aurora_core.tracer import CYAN, MAGENTA, trace_step
            trace_step(
                "4/5",
                "Tool Request",
                "",
                f"LLM requested {len(tool_calls)} tool call(s) (Round {round_num + 1})",
                f"Tools: {[tc.get('function', {}).get('name') for tc in tool_calls]}",
                MAGENTA,
            )

            # Append the assistant's tool_call message to conversation
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": tool_calls,
            })

            # Execute each tool call and collect results
            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                tool_args = func.get("arguments", {})
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except json.JSONDecodeError:
                        tool_args = {}

                call_id = str(uuid.uuid4())[:8]

                # For win_automation, browser, and code_intel handlers that use 'action' key,
                # set it to the tool_name if not already present
                if tool_name in ("open_app", "get_window_list", "focus_window", "get_system_info"):
                    tool_args["action"] = tool_name
                    actual_tool = "win_automation"
                elif tool_name in ("open_url", "get_page_content", "search_web"):
                    tool_args["action"] = tool_name
                    actual_tool = "browser"
                elif tool_name in ("get_symbol_map", "find_definitions", "find_callers"):
                    tool_args["action"] = tool_name
                    actual_tool = "code_intel"
                elif tool_name in ("take_screenshot", "read_screen_text", "inspect_image"):
                    tool_args["action"] = tool_name
                    actual_tool = "vision"
                elif tool_name in ("generate_image", "image_gen"):
                    actual_tool = "image_gen"
                else:
                    actual_tool = tool_name

                tool_result = await self._execute_tool(call_id, session_id, actual_tool, tool_args)

                # Append tool result message to conversation
                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                })

                trace_step(
                    "5b",
                    "Tool Completed",
                    "",
                    f"Tool '{tool_name}' finished execution",
                    f"Success: {tool_result.get('ok')} | Data: {str(tool_result.get('data'))[:100]}...",
                    CYAN,
                )

            # Re-call LLM with tool results appended to messages
            trace_step(
                "6",
                "AI Re-Invoke",
                "",
                "Feeding tool execution results back into LLM context",
                f"Round {round_num + 1} complete ➔ fetching final answer",
                CYAN,
            )

        # 1. Publish stats update before completion
        await self._ctx.bus.publish(
            "stats.update",
            {
                "active_model": self.get_selected_model(),
                "total_tokens": self.total_tokens_spent,
            },
        )

        # 2. Terminal chat.complete event to close streaming cursor in UI
        final_text = result.get("text", "") if result else ""
        final_tokens = result.get("tokens_count", 0) if result else 0
        await self._ctx.bus.publish(
            "chat.complete",
            {
                "session_id": session_id,
                "total_tokens": final_tokens,
                "text": final_text,
            },
        )

    async def _execute_tool(
        self, call_id: str, session_id: str, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool via EventBus and wait for the result."""
        assert self._ctx is not None

        # Create a future to wait for the tool result
        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._tool_futures[call_id] = future

        try:
            # Publish the tool execution request
            await self._ctx.bus.publish(
                "tool.execute",
                {
                    "call_id": call_id,
                    "session_id": session_id,
                    "tool": tool_name,
                    "args": args,
                },
            )

            # Wait for result with timeout
            try:
                result = await asyncio.wait_for(future, timeout=60.0)
                return result
            except asyncio.TimeoutError:
                logger.warning("Tool %s timed out (call_id=%s)", tool_name, call_id)
                return {"ok": False, "data": None, "error": f"Tool '{tool_name}' timed out after 60s"}

        finally:
            self._tool_futures.pop(call_id, None)

    # ------------------------------------------------------------------
    # Ollama API call (streaming or non-streaming for tool calls)
    # ------------------------------------------------------------------

    async def _call_ollama(
        self,
        session_id: str,
        model: str,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Call Ollama /api/chat. Streams text tokens to UI.

        Returns:
            dict with keys:
              - 'text': full response text (may be empty if tool_calls)
              - 'tool_calls': list of tool call dicts, or None if text response
            None on error (fallback already emitted).
        """
        assert self._ctx is not None
        endpoint = f"{self.base_url}/api/chat"

        # Build request body with forced GPU offloading and tuned context
        req_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_gpu": getattr(self, "num_gpu", 99),
                "num_ctx": getattr(self, "num_ctx", 4096),
            },
        }
        if tool_schemas:
            req_body["tools"] = tool_schemas

        tokens_count = 0
        full_text = ""
        tool_calls: list[dict[str, Any]] = []

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s)) as client:
                async with client.stream("POST", endpoint, json=req_body) as response:
                    if response.status_code != 200:
                        err_body = await response.aread()
                        err_text = err_body.decode("utf-8", errors="replace")
                        err_msg = f"Ollama HTTP {response.status_code}: {err_text}"
                        self._ctx.logger.error("llm_ollama error: %s", err_msg)
                        sys.stderr.write(f"[API ERROR] {err_msg}\n")
                        sys.stderr.flush()
                        await self._emit_fallback(
                            session_id,
                            f"⚠️ Error from Ollama: {err_text}. Please check model '{model}'."
                        )
                        return None

                    async for line in response.aiter_lines():
                        if not line or not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                            msg_chunk = chunk.get("message", {})

                            # Check for tool calls in the response
                            chunk_tool_calls = msg_chunk.get("tool_calls")
                            if chunk_tool_calls:
                                tool_calls.extend(chunk_tool_calls)

                            token = msg_chunk.get("content", "")
                            if token:
                                tokens_count += 1
                                full_text += token
                                # Print stream indicator token to terminal
                                sys.stdout.write(token)
                                sys.stdout.flush()
                                await self._ctx.bus.publish(
                                    "llm.token",
                                    {"token": token, "session_id": session_id},
                                )
                        except json.JSONDecodeError:
                            continue

        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            self._ctx.logger.warning("llm_ollama connection failed: %s", exc)
            err_line = f"\n[API ERROR] Connection failed to {self.base_url} ({exc})\n"
            sys.stderr.write(err_line)
            sys.stderr.flush()
            fallback = (
                f"⚠️ Unable to connect to local Ollama service at {self.base_url}.\n"
                f"Please ensure Ollama is running (`ollama serve`), then try again."
            )
            await self._emit_fallback(session_id, fallback)
            return None
        except Exception as exc:
            self._ctx.logger.error("llm_ollama unexpected error: %s", exc, exc_info=True)
            err_line = f"\n[API ERROR] {exc}\n"
            sys.stderr.write(err_line)
            sys.stderr.flush()
            await self._emit_fallback(session_id, f"⚠️ Generation error: {exc}")
            return None

        self.total_tokens_spent += tokens_count
        comp_msg = (
            f"\n-------------------------------------------------------\n"
            f"[API COMPLETE] Model={model} | Tokens Generated={tokens_count} | Cumulative Spent={self.total_tokens_spent}\n"
            f"-------------------------------------------------------\n"
        )
        sys.stderr.write(comp_msg)
        sys.stderr.flush()

        # If we got tool calls, don't emit chat.complete yet — the loop continues
        if tool_calls:
            return {"text": full_text, "tool_calls": tool_calls}

        # Normal text completion
        return {"text": full_text, "tool_calls": None, "tokens_count": tokens_count}

    async def _emit_fallback(self, session_id: str, message: str) -> None:
        assert self._ctx is not None
        await self._ctx.bus.publish(
            "llm.token",
            {"token": message, "session_id": session_id},
        )
        await self._ctx.bus.publish(
            "chat.complete",
            {
                "session_id": session_id,
                "total_tokens": 1,
                "text": message,
            },
        )


PLUGIN = OllamaPlugin
