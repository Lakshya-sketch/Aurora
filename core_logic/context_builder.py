"""Context Builder — Assembles Authoritative System Context from RAG, AST, and Memory.

Phase 4.2 Vector RAG & Phase 4.1 Code Intelligence.
Injects retrieved knowledge into the LLM system instructions with explicit attribution.
"""

from __future__ import annotations

from typing import Any


class ContextBuilder:
    """Builds formatted reference context strings for prompt injection."""

    def format_rag_chunks(self, chunks: list[dict[str, Any]], max_chars: int = 8000) -> str:
        """Format retrieved RAG document chunks with attribution and similarity scores."""
        if not chunks:
            return ""

        lines = [
            f"=== RETRIEVED DOCUMENT CONTEXT ({len(chunks)} chunks) ===",
        ]
        total_chars = 0
        for i, c in enumerate(chunks, 1):
            metadata = c.get("metadata", {})
            filename = metadata.get("filename", "unknown")
            page = metadata.get("page_number", metadata.get("page", "?"))
            score = c.get("score")
            score_str = f" | Relevance: {score:.3f}" if score is not None else ""

            header = f"\n[Chunk {i} | Source: {filename} (Page {page}){score_str}]"
            content = c.get("text", "").strip()
            block = f"{header}\n{content}\n"

            if total_chars + len(block) > max_chars:
                lines.append(f"\n... [{len(chunks) - i + 1} additional relevant chunks truncated] ...\n")
                break

            lines.append(block)
            total_chars += len(block)

        lines.append("=== END OF RETRIEVED DOCUMENT CONTEXT ===")
        return "\n".join(lines)

    def format_ast_symbols(self, symbols: dict[str, Any] | list[dict[str, Any]]) -> str:
        """Format code intelligence symbol maps into concise structural context."""
        if not symbols:
            return ""

        file_items = symbols if isinstance(symbols, list) else [symbols]
        lines = ["=== CODE STRUCTURE & SYMBOLS (AST) ==="]

        for item in file_items:
            file_name = item.get("file", "unknown")
            classes = item.get("classes", [])
            functions = item.get("functions", [])

            lines.append(f"\nFile: {file_name}")
            if classes:
                lines.append("Classes:")
                for c in classes:
                    methods = [m.get("signature", m.get("name")) for m in c.get("methods", [])]
                    method_str = f": {', '.join(methods)}" if methods else ""
                    lines.append(f"  - class {c.get('name')} (L{c.get('line_start')}-L{c.get('line_end')}){method_str}")

            if functions:
                lines.append("Functions:")
                for f in functions:
                    lines.append(f"  - {f.get('signature', f.get('name'))} (Line {f.get('line_start')})")

        lines.append("\n=== END OF CODE STRUCTURE ===")
        return "\n".join(lines)

    def format_memories(self, memories: list[dict[str, Any]], max_chars: int = 4000) -> str:
        """Format recalled conversational and factual memories with attribution."""
        if not memories:
            return ""

        lines = [
            f"=== LONG-TERM MEMORY & RECALLED FACTS ({len(memories)} memories) ===",
        ]
        total_chars = 0
        for i, m in enumerate(memories, 1):
            sess = m.get("session_id") or "global"
            ts = m.get("timestamp", "")[:19]
            scope = m.get("scope", "memory")
            score = m.get("score")
            score_str = f" | Score: {score:.3f}" if score is not None else ""

            header = f"\n[Memory {i} | Scope: {scope} | Session: {sess} | {ts}{score_str}]"
            content = m.get("text", "").strip()
            block = f"{header}\n{content}\n"

            if total_chars + len(block) > max_chars:
                lines.append(f"\n... [{len(memories) - i + 1} additional past memories omitted] ...\n")
                break

            lines.append(block)
            total_chars += len(block)

        lines.append("=== END OF LONG-TERM MEMORY ===")
        return "\n".join(lines)

    def build_context(
        self,
        rag_chunks: list[dict[str, Any]] | None = None,
        ast_symbols: dict[str, Any] | list[dict[str, Any]] | None = None,
        memories: list[dict[str, Any]] | None = None,
    ) -> str:
        """Assemble all available contextual knowledge into a coherent reference block."""
        blocks: list[str] = []

        if memories:
            mem_block = self.format_memories(memories)
            if mem_block:
                blocks.append(mem_block)

        if rag_chunks:
            rag_block = self.format_rag_chunks(rag_chunks)
            if rag_block:
                blocks.append(rag_block)

        if ast_symbols:
            ast_block = self.format_ast_symbols(ast_symbols)
            if ast_block:
                blocks.append(ast_block)

        return "\n\n".join(blocks).strip()
