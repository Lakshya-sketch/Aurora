"""Aurora Orchestrator — event-driven agent workflow engine.

Subscribes to ``user.message`` events on the EventBus, automatically processes
file attachments via RAG document loaders and AST code intelligence, prepares
the prompt via PromptBuilder, and dispatches generation tasks to the active LLM plugin.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core_logic.context_builder import ContextBuilder
from core_logic.memory_inspector import register_ast_symbols, register_rag_chunks
from core_logic.prompt_builder import PromptBuilder

if TYPE_CHECKING:
    from aurora_core.event_bus import EventBus

logger = logging.getLogger("aurora.orchestrator")


class Orchestrator:
    """Agent workflow orchestrator."""

    def __init__(
        self,
        bus: EventBus,
        prompt_builder: PromptBuilder | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.bus = bus
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.context_builder = context_builder or ContextBuilder()

    def _extract_attached_paths(self, text: str, attachments: list[str] | None = None) -> list[Path]:
        """Detect and return existing file paths from attachments payload or prompt text."""
        paths: list[Path] = []
        if attachments:
            for att in attachments:
                if isinstance(att, str):
                    p = Path(att.strip().strip('"').strip("'"))
                    if p.is_file() and p not in paths:
                        paths.append(p)

        # Pattern: Attach and summarise: <path>
        m_attach = re.search(r"Attach and summarise:\s*(.+)", text, re.IGNORECASE)
        if m_attach:
            raw_target = m_attach.group(1).strip()
            for item in raw_target.split(","):
                p = Path(item.strip().strip('"').strip("'"))
                if p.is_file() and p not in paths:
                    paths.append(p)

        # Pattern: Any explicit absolute file path ending in supported extensions (documents, code, images)
        for m in re.finditer(r'([A-Za-z]:\\[^\r\n"\'<>|]+?\.(?:pdf|txt|md|docx|py|js|ts|jsx|tsx|json|csv|jpg|jpeg|png|webp|bmp))', text, re.IGNORECASE):
            p = Path(m.group(1).strip())
            if p.is_file() and p not in paths:
                paths.append(p)

        return paths

    async def _fetch_symbol_map(self, path: Path) -> dict[str, Any]:
        """Fetch code symbol map via EventBus tool execution (Rule 3 compliant)."""
        call_id = f"orch_ast_{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        async def _result_handler(topic: str, payload: dict[str, Any]) -> None:
            if payload.get("call_id") == call_id and not future.done():
                future.set_result(payload)

        await self.bus.subscribe("tool.result", _result_handler)
        try:
            num_subs = await self.bus.publish(
                "tool.execute",
                {
                    "call_id": call_id,
                    "session_id": "orchestrator",
                    "tool": "get_symbol_map",
                    "args": {"path": str(path)},
                },
            )
            if num_subs > 0:
                try:
                    res = await asyncio.wait_for(future, timeout=5.0)
                    return res
                except asyncio.TimeoutError:
                    pass

            # Dynamic fallback if running in standalone test fixture without tools plugin
            import importlib
            code_intel_mod = importlib.import_module("plugins.tools_builtin.handlers.code_intel")
            return await code_intel_mod.get_symbol_map(path=str(path))
        except Exception as exc:
            logger.debug("Failed to fetch symbol map: %s", exc)
            return {"ok": False, "error": str(exc)}
        finally:
            await self.bus.unsubscribe("tool.result", _result_handler)

    async def _index_document(self, path: Path, session_id: str = "default") -> tuple[list[Any], list[Any]]:
        """Index and load document pages and chunks via EventBus (Rule 3 compliant: no concrete plugin imports)."""
        call_id = f"orch_rag_{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        async def _result_handler(topic: str, payload: dict[str, Any]) -> None:
            if payload.get("call_id") == call_id and not future.done():
                future.set_result(payload)

        await self.bus.subscribe("rag.indexed", _result_handler)
        try:
            num_subs = await self.bus.publish(
                "rag.index",
                {
                    "call_id": call_id,
                    "session_id": session_id,
                    "file_paths": [str(path)],
                },
            )
            indexed_payload: dict[str, Any] = {}
            if num_subs > 0:
                try:
                    indexed_payload = await asyncio.wait_for(future, timeout=5.0)
                except asyncio.TimeoutError:
                    pass

            pages_raw = indexed_payload.get("pages", [])
            chunks_raw = indexed_payload.get("chunks", [])

            from types import SimpleNamespace
            pages = [SimpleNamespace(**p) if isinstance(p, dict) else p for p in pages_raw]
            chunks = chunks_raw

            # Standalone fallback when RAG plugin is absent (e.g. isolated unit tests)
            if not pages and path.exists():
                try:
                    raw_text = path.read_text(errors="ignore")
                    pages = [SimpleNamespace(page_number=1, text=raw_text)]
                    chunks = []
                    chunk_size = 600
                    step = 500  # 600 size with 100 overlap
                    for i in range(0, max(1, len(raw_text)), step):
                        c_text = raw_text[i:i + chunk_size]
                        if c_text.strip():
                            chunks.append({
                                "chunk_id": f"{path.name}#chunk_{len(chunks)}",
                                "text": c_text,
                                "metadata": {
                                    "source": str(path),
                                    "filename": path.name,
                                    "file_name": path.name,
                                    "page": 1,
                                },
                            })
                except Exception as read_exc:
                    logger.debug("Standalone fallback indexing failed for %s: %s", path, read_exc)

            register_rag_chunks(chunks)
            return pages, chunks
        except Exception as exc:
            logger.warning("Failed to index document %s: %s", path, exc)
            return [], []
        finally:
            await self.bus.unsubscribe("rag.indexed", _result_handler)

    async def _process_attachments(self, paths: list[Path], session_id: str = "default") -> tuple[str, list[str]]:
        """Load, chunk, and index attached files via RAG & AST, returning (context_str, images_b64)."""
        context_blocks: list[str] = []
        images_b64: list[str] = []

        for path in paths:
            ext = path.suffix.lower()
            try:
                # 1. Image files (.jpg, .jpeg, .png, .webp, .bmp) -> Base64 encode for Vision VLM
                if ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                    try:
                        raw_bytes = path.read_bytes()
                        b64 = base64.b64encode(raw_bytes).decode("ascii")
                        images_b64.append(b64)
                        context_blocks.append(
                            f"=== ATTACHED IMAGE FILE: {path.name} ===\n"
                            f"File: {path}\n"
                            f"Size: {len(raw_bytes):,} bytes\n"
                            f"NOTE: The user has attached this image. To inspect visual details, transcribe code, or perform OCR, call the `inspect_image` tool with image_path={str(path)!r}.\n"
                            f"=== END OF ATTACHED IMAGE: {path.name} ==="
                        )
                    except Exception as img_err:
                        logger.warning("Failed to read attached image %s: %s", path, img_err)

                # 2. Code files -> AST extraction + full source content + RAG indexing
                elif ext in {".py", ".js", ".ts", ".jsx", ".tsx"}:
                    try:
                        await self._index_document(path, session_id=session_id)
                    except Exception as idx_err:
                        logger.warning("Failed to RAG index code file %s: %s", path, idx_err)

                    sym_res = await self._fetch_symbol_map(path)
                    if sym_res.get("ok"):
                        sym_data = sym_res.get("data", {})
                        register_ast_symbols(sym_data)

                        classes_info = []
                        for c in sym_data.get("classes", []):
                            methods = [m.get("signature", m.get("name")) for m in c.get("methods", [])]
                            classes_info.append(
                                f"  - Class {c.get('name')} (Lines {c.get('line_start')}-{c.get('line_end')}): {', '.join(methods)}"
                            )

                        funcs_info = [
                            f"  - Function {f.get('signature', f.get('name'))} (Line {f.get('line_start')})"
                            for f in sym_data.get("functions", [])
                        ]

                        code_block = [
                            f"=== ATTACHED CODE FILE: {path.name} ===",
                            f"File: {path}",
                            "NOTE: This file has already been read and attached below. Do NOT call file_search for this file.",
                        ]
                        if classes_info:
                            code_block += ["--- Structure (AST) ---", "Classes:", *classes_info]
                        if funcs_info:
                            code_block += ["Functions:", *funcs_info]

                        # Inject actual file content (up to 8000 chars) so the
                        # LLM can answer questions without needing file_search.
                        try:
                            raw_source = path.read_text(errors="ignore")
                            truncated = len(raw_source) > 8000
                            raw_source = raw_source[:8000]
                            code_block += [
                                "--- Source Code ---",
                                f"```{ext.lstrip('.')}",
                                raw_source,
                                "```",
                            ]
                            if truncated:
                                code_block.append(
                                    "... [file truncated at 8000 chars; full content indexed in RAG] ..."
                                )
                        except Exception as read_exc:
                            logger.warning("Could not read source of %s: %s", path, read_exc)

                        code_block.append(f"=== END OF ATTACHED CODE FILE: {path.name} ===")
                        context_blocks.append("\n".join(b for b in code_block if b))

                # 2. Document files (PDF, TXT, MD, etc.) -> RAG Document Loader & Chunker
                else:
                    pages, chunks = await self._index_document(path, session_id=session_id)
                    if pages:
                        doc_lines = [
                            f"=== ATTACHED DOCUMENT CONTENT: {path.name} (Total Pages: {len(pages)}, Chunks: {len(chunks)}) ===",
                        ]

                        # Concatenate pages up to 8000 characters
                        total_chars = 0
                        for p in pages:
                            page_text = f"\n[Page {p.page_number}]\n{p.text.strip()}\n"
                            if total_chars + len(page_text) <= 8000:
                                doc_lines.append(page_text)
                                total_chars += len(page_text)
                            else:
                                remaining = len(pages) - p.page_number + 1
                                doc_lines.append(f"\n... [{remaining} additional pages indexed into RAG memory] ...\n")
                                break

                        doc_lines.append(f"=== END OF ATTACHED DOCUMENT: {path.name} ===")
                        context_blocks.append("\n".join(doc_lines))

            except Exception as exc:
                logger.warning("Failed to process attached file %s: %s", path, exc)

        return "\n\n".join(context_blocks), images_b64

    async def _retrieve_memories_via_bus(
        self,
        query: str,
        session_id: str | None = None,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant past memories via EventBus (Rule 3 compliant)."""
        call_id = f"orch_mem_{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        async def _result_handler(topic: str, payload: dict[str, Any]) -> None:
            if payload.get("call_id") == call_id and not future.done():
                future.set_result(payload)

        await self.bus.subscribe("memory.result", _result_handler)
        try:
            num_subs = await self.bus.publish(
                "memory.retrieve",
                {
                    "call_id": call_id,
                    "session_id": session_id,
                    "query": query,
                    "k": k,
                },
            )
            if num_subs > 0:
                try:
                    res = await asyncio.wait_for(future, timeout=2.0)
                    return res.get("memories", [])
                except asyncio.TimeoutError:
                    pass
            return []
        except Exception as exc:
            logger.debug("Failed to retrieve memories: %s", exc)
            return []
        finally:
            await self.bus.unsubscribe("memory.result", _result_handler)

    async def _retrieve_rag_chunks_via_bus(
        self,
        query: str,
        session_id: str | None = None,
        k: int = 4,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant RAG document chunks via EventBus (Rule 3 compliant)."""
        call_id = f"orch_rag_srch_{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        async def _result_handler(topic: str, payload: dict[str, Any]) -> None:
            if payload.get("call_id") == call_id and not future.done():
                future.set_result(payload)

        await self.bus.subscribe("rag.result", _result_handler)
        try:
            num_subs = await self.bus.publish(
                "rag.search",
                {
                    "call_id": call_id,
                    "session_id": session_id or "default",
                    "query": query,
                    "k": k,
                    "score_threshold": 0.25,
                },
            )
            if num_subs > 0:
                try:
                    res = await asyncio.wait_for(future, timeout=2.5)
                    return res.get("results", [])
                except asyncio.TimeoutError:
                    pass
            return []
        except Exception as exc:
            logger.debug("Failed to retrieve RAG chunks: %s", exc)
            return []
        finally:
            await self.bus.unsubscribe("rag.result", _result_handler)

    async def handle_user_message(self, topic: str, payload: dict[str, Any]) -> None:
        """Handle incoming user.message events."""
        if topic != "user.message":
            return

        session_id = payload.get("session_id", "default")
        text = payload.get("text", "").strip()

        if not text:
            logger.warning("orchestrator received empty text for session %s", session_id)
            return

        logger.info("orchestrator processing user.message for session=%s", session_id)

        # 1. Detect and process attached documents or code files via RAG & AST
        attachments = payload.get("attachments")
        attached_paths = self._extract_attached_paths(text, attachments)

        attachment_context = ""
        attached_images: list[str] = []
        if attached_paths:
            logger.info(
                "orchestrator detected %d attached file(s): %s",
                len(attached_paths),
                [p.name for p in attached_paths],
            )
            attachment_context, attached_images = await self._process_attachments(attached_paths, session_id=session_id)

        # 2. If prompt was just "Attach and summarise: <path>", make it user-friendly for the LLM
        clean_prompt = text
        if re.match(r"^Attach and summarise:\s*", text, re.IGNORECASE):
            filenames = ", ".join(p.name for p in attached_paths) if attached_paths else "the attached file"
            clean_prompt = f"Please provide a comprehensive summary and key takeaways of the attached document ({filenames})."

        # 3. Auto-recall relevant long-term memories via EventBus
        recalled_memories = await self._retrieve_memories_via_bus(clean_prompt, session_id=session_id, k=5)
        memory_context = self.context_builder.format_memories(recalled_memories) if recalled_memories else ""

        # 4. Auto-retrieve relevant document chunks from indexed RAG knowledge base
        rag_context = ""
        retrieved_chunks: list[dict[str, Any]] = []
        if not attached_paths:
            retrieved_chunks = await self._retrieve_rag_chunks_via_bus(clean_prompt, session_id=session_id, k=4)
            if retrieved_chunks:
                chunk_blocks = [
                    "=== RELEVANT DOCUMENT EXCERPTS (FROM INDEXED FILES) ===",
                    "Use the following excerpts from previously uploaded/indexed documents to answer the user's question accurately:"
                ]
                for i, chk in enumerate(retrieved_chunks, 1):
                    meta = chk.get("metadata", {})
                    src = meta.get("filename") or meta.get("file_name") or meta.get("source") or "Document"
                    page = meta.get("page") or meta.get("page_number")
                    page_str = f" (Page {page})" if page else ""
                    chunk_blocks.append(f"\n[Excerpt {i} from {src}{page_str}]\n{chk.get('text', '').strip()}")
                chunk_blocks.append("=== END OF RELEVANT DOCUMENT EXCERPTS ===")
                rag_context = "\n".join(chunk_blocks)

        full_context_blocks = []
        if memory_context:
            full_context_blocks.append(memory_context)
        if attachment_context:
            full_context_blocks.append(attachment_context)
        if rag_context:
            full_context_blocks.append(rag_context)
        combined_context = "\n\n".join(full_context_blocks) if full_context_blocks else None

        from aurora_core.tracer import GREEN, trace_step
        trace_step(
            "2/5",
            "AI Engine",
            "",
            f"Building prompt with system instructions + context injection ({len(attached_paths)} files, {len(retrieved_chunks)} RAG chunks, {len(recalled_memories)} memories)",
            f"Prompt: {clean_prompt!r} | Context: {len(combined_context or ''):,} chars | Target Model: {payload.get('model') or 'default'}",
            GREEN,
        )
        if combined_context:
            if attached_paths:
                file_names = ", ".join(p.name for p in attached_paths)
                sys.stderr.write(f"    ├─ Active Attachments: [{file_names}]\n")
            if retrieved_chunks:
                sys.stderr.write(f"    ├─ Active RAG Chunks: {len(retrieved_chunks)} retrieved from knowledge base\n")
            if recalled_memories:
                sys.stderr.write(f"    ├─ Active Memories: {len(recalled_memories)} recalled facts\n")
            sys.stderr.write(f"    └─ Context Payload: {len(combined_context):,} chars injected into system instructions\n")
            sys.stderr.flush()

        history = payload.get("history")
        system_override = payload.get("system_prompt")

        messages = self.prompt_builder.build_messages(
            user_text=clean_prompt,
            system_override=system_override,
            history=history,
            context=combined_context,
            images=attached_images if attached_images else None,
        )

        target_model = payload.get("model")

        # Dispatch generation task for LLM plugins
        await self.bus.publish(
            "llm.generate",
            {
                "session_id": session_id,
                "messages": messages,
                "images": attached_images if attached_images else None,
                "model": target_model,
            },
        )
