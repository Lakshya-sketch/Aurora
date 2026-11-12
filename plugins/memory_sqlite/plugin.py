"""Memory SQLite Plugin — Dual Mode: Standard (A) & Turbo Cache (B).

Supports:
- Installation A (Standard Mode): Direct SQLite disk persistence in `data/aurora.db`.
- Installation B (Turbo Cache Mode): In-memory session cache (`sqlite3 :memory:`) with
  1-minute periodic batch write-through, LRU 5-session eviction, and flush on session close/switch.

Subscribes to:
- ``memory.store``: Store arbitrary facts, notes, or conversation turns.
- ``memory.retrieve``: Retrieve relevant past memories using hybrid FTS5 + vector search.
- ``memory.history``: Fetch chronological chat history for a session.
- ``user.message``: Automatically persists incoming user prompts to messages & memories.
- ``tool.result``: Automatically records tool execution audit logs.
- ``chat.complete``: Automatically records assistant responses into long-term history.
- ``session.close``: Immediately flushes session in-memory cache to disk and evicts.
- ``session.switch``: Flushes previous active session and preloads the current session.
- ``memory.flush_now``: Explicit manual flush trigger.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from aurora_core.plugin_base import BasePlugin, PluginContext
from plugins.memory_sqlite.cache_manager import SessionCacheManager
from plugins.memory_sqlite.db import DatabaseManager
from plugins.memory_sqlite.vectorizer import MemoryVectorizer

logger = logging.getLogger("aurora.plugin.memory_sqlite")


class MemorySqlitePlugin(BasePlugin):
    """SQLite-backed conversation and semantic memory plugin with optional in-memory Turbo Cache."""

    name = "memory_sqlite"
    version = "0.2.0"
    capabilities = ["memory.store", "memory.retrieve", "memory.history"]
    requires: list[str] = []
    subscribes = [
        "memory.store",
        "memory.retrieve",
        "memory.history",
        "user.message",
        "tool.result",
        "chat.complete",
        "session.close",
        "session.switch",
        "session.delete",
        "memory.flush_now",
        "database.clear",
        "memory.clear",
    ]

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self.db: DatabaseManager | None = None
        self.vectorizer: MemoryVectorizer | None = None
        self.mode: str = "standard"
        self.cache: SessionCacheManager | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_interval_s: float = 60.0

    async def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        db_path = ctx.config.get("aurora", {}).get("db_path")
        if not db_path:
            data_dir = ctx.config.get("aurora", {}).get("data_dir", "data")
            db_path = str(Path(data_dir) / "aurora.db")

        # Determine mode: "standard" (Installation A) vs "turbo_cache" (Installation B)
        self.mode = str(ctx.config.get("memory", {}).get("mode", "standard")).lower()

        # Ensure parent directory exists before opening the DB.
        try:
            resolved = Path(db_path).resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self.db = DatabaseManager(db_path=str(resolved))
            ctx.logger.info("memory_sqlite main database ready: db_path=%s", resolved)
        except Exception as exc:
            ctx.logger.warning(
                "memory_sqlite: cannot open '%s' (%s) — falling back to :memory: store",
                db_path,
                exc,
            )
            self.db = DatabaseManager(db_path=":memory:")
            ctx.logger.info("memory_sqlite main database ready: db_path=:memory: (ephemeral)")

        self.vectorizer = MemoryVectorizer()

        # Configure Turbo Cache (Installation B)
        if self.mode == "turbo_cache":
            cache_cfg = ctx.config.get("memory", {}).get("cache", {})
            max_cached = int(cache_cfg.get("max_cached_sessions", 5))
            self._flush_interval_s = float(cache_cfg.get("sync_interval_s", 60.0))
            self.cache = SessionCacheManager(max_cached_sessions=max_cached)
            self._flush_task = asyncio.create_task(self._periodic_flush_loop())
            ctx.logger.info(
                "memory_sqlite running in TURBO CACHE mode (max_sessions=%d, sync_interval=%.1fs)",
                max_cached,
                self._flush_interval_s,
            )
        else:
            ctx.logger.info("memory_sqlite running in STANDARD mode (direct disk persistence)")

    async def shutdown(self) -> None:
        if self._ctx:
            self._ctx.logger.info("memory_sqlite shutting down (mode=%s)", self.mode)

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        if self.cache and self.db:
            self.cache.close_all(main_db=self.db)
            self.cache = None

        if self.db:
            self.db.close()
            self.db = None
        self._ctx = None

    async def switch_mode(self, new_mode: str) -> bool:
        """Switch runtime memory mode between 'standard' and 'turbo_cache'. Returns True if mode changed."""
        clean_mode = str(new_mode).lower().strip()
        if clean_mode not in ("standard", "turbo_cache"):
            raise ValueError(f"Invalid mode '{clean_mode}'. Must be 'standard' or 'turbo_cache'.")
        if self.mode == clean_mode:
            return False

        if clean_mode == "turbo_cache":
            cache_cfg = (self._ctx.config if self._ctx else {}).get("memory", {}).get("cache", {})
            max_cached = int(cache_cfg.get("max_cached_sessions", 5))
            self._flush_interval_s = float(cache_cfg.get("sync_interval_s", 60.0))
            self.cache = SessionCacheManager(max_cached_sessions=max_cached)
            self._flush_task = asyncio.create_task(self._periodic_flush_loop())
            self.mode = "turbo_cache"
            if self._ctx:
                self._ctx.logger.info("memory_sqlite switched to TURBO CACHE mode")
        else:
            # Switching to standard: cancel flush task, flush dirty cache, and close
            if self._flush_task:
                self._flush_task.cancel()
                try:
                    await self._flush_task
                except asyncio.CancelledError:
                    pass
                self._flush_task = None
            if self.cache and self.db:
                self.cache.close_all(main_db=self.db)
                self.cache = None
            self.mode = "standard"
            if self._ctx:
                self._ctx.logger.info("memory_sqlite switched to STANDARD mode")
        return True

    def _get_target_db(self, session_id: str | None = None) -> DatabaseManager:
        """Return the target database for operations based on mode and session."""
        if self.mode == "turbo_cache" and self.cache and session_id:
            return self.cache.get_session_db(session_id, main_db=self.db)
        assert self.db is not None
        return self.db

    async def _periodic_flush_loop(self) -> None:
        """Background worker that flushes dirty in-memory sessions every 60s."""
        while True:
            try:
                await asyncio.sleep(self._flush_interval_s)
                if self.cache and self.db:
                    dirty = self.cache.get_dirty_sessions()
                    if dirty:
                        logger.info(
                            "Periodic 1-min timer: flushing %d dirty session(s) to disk (%s)",
                            len(dirty),
                            dirty,
                        )
                        self.cache.flush_all(self.db)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Error in memory_sqlite periodic flush loop: %s", exc)

    async def handle_event(self, topic: str, payload: dict[str, Any]) -> None:
        assert self._ctx is not None
        assert self.db is not None and self.vectorizer is not None

        # 1. Store explicit memory
        if topic == "memory.store":
            text = payload.get("text", "").strip()
            if not text:
                return

            session_id = payload.get("session_id")
            scope = payload.get("scope", "conversation")
            metadata = payload.get("metadata", {})
            call_id = payload.get("call_id", "")

            embedding = self.vectorizer.embed_text(text)
            target_db = self._get_target_db(session_id)
            row_id = target_db.store_memory(
                text=text,
                session_id=session_id,
                scope=scope,
                embedding=embedding,
                metadata=metadata,
            )
            if self.mode == "turbo_cache" and self.cache and session_id:
                self.cache.mark_dirty(session_id)

            await self._ctx.bus.publish(
                "memory.stored",
                {
                    "call_id": call_id,
                    "session_id": session_id,
                    "id": row_id,
                    "ok": True,
                },
            )
            return

        # 2. Retrieve memories (hybrid semantic + FTS5)
        if topic == "memory.retrieve":
            query = payload.get("query", "").strip()
            session_id = payload.get("session_id")
            cross_session = payload.get("cross_session", True)
            k = payload.get("k", 5)
            call_id = payload.get("call_id", "")

            if not query:
                memories: list[dict[str, Any]] = []
            else:
                q_emb = self.vectorizer.embed_text(query)
                target_db = self._get_target_db(session_id)
                memories = target_db.search_memories_hybrid(
                    query=query,
                    query_embedding=q_emb,
                    session_id=session_id,
                    cross_session=cross_session,
                    k=k,
                )
                # If turbo_cache and cross_session is requested and we need more memories,
                # merge with main_db past memories without duplicate texts
                if self.mode == "turbo_cache" and cross_session and self.db and len(memories) < k:
                    seen_texts = {m["text"] for m in memories}
                    main_mems = self.db.search_memories_hybrid(
                        query=query,
                        query_embedding=q_emb,
                        session_id=session_id,
                        cross_session=True,
                        k=k,
                    )
                    for m in main_mems:
                        if m["text"] not in seen_texts:
                            seen_texts.add(m["text"])
                            memories.append(m)
                            if len(memories) >= k:
                                break

            await self._ctx.bus.publish(
                "memory.result",
                {
                    "call_id": call_id,
                    "session_id": session_id,
                    "query": query,
                    "memories": memories,
                    "count": len(memories),
                    "ok": True,
                },
            )
            return

        # 3. Retrieve session message history
        if topic == "memory.history":
            session_id = payload.get("session_id", "default")
            limit = payload.get("limit", 20)
            call_id = payload.get("call_id", "")

            target_db = self._get_target_db(session_id)
            messages = target_db.get_messages(session_id=session_id, limit=limit)

            await self._ctx.bus.publish(
                "memory.history_result",
                {
                    "call_id": call_id,
                    "session_id": session_id,
                    "messages": messages,
                    "count": len(messages),
                    "ok": True,
                },
            )
            return

        # 4. Auto-save user message to history & memories
        if topic == "user.message":
            text = payload.get("text", "").strip()
            session_id = payload.get("session_id", "default")
            if text:
                target_db = self._get_target_db(session_id)
                target_db.store_message(session_id, "user", text)
                emb = self.vectorizer.embed_text(text)
                target_db.store_memory(
                    text=text,
                    session_id=session_id,
                    scope="conversation",
                    embedding=emb,
                    metadata={"role": "user"},
                )
                if self.mode == "turbo_cache" and self.cache:
                    self.cache.mark_dirty(session_id)
            return

        # 5. Record tool invocation audit
        if topic == "tool.result":
            tool = payload.get("tool", "unknown")
            call_id = payload.get("call_id", "")
            session_id = payload.get("session_id", "default")
            output = payload.get("data") if payload.get("ok") else payload.get("error")
            ok = payload.get("ok", True)
            target_db = self._get_target_db(session_id)
            target_db.store_tool_invocation(
                session_id=session_id,
                tool=tool,
                call_id=call_id,
                args=payload.get("args"),
                output=output,
                ok=ok,
            )
            if self.mode == "turbo_cache" and self.cache:
                self.cache.mark_dirty(session_id)
            return

        # 6. Record completed assistant turn
        if topic == "chat.complete":
            session_id = payload.get("session_id", "default")
            text = payload.get("text", "").strip()
            if text:
                target_db = self._get_target_db(session_id)
                target_db.store_message(session_id, "assistant", text)
                emb = self.vectorizer.embed_text(text)
                target_db.store_memory(
                    text=text,
                    session_id=session_id,
                    scope="conversation",
                    embedding=emb,
                    metadata={"role": "assistant"},
                )
                if self.mode == "turbo_cache" and self.cache:
                    self.cache.mark_dirty(session_id)
            return

        # 7. Session close: flush to disk and evict from RAM
        if topic == "session.close":
            session_id = payload.get("session_id")
            if self.mode == "turbo_cache" and self.cache and session_id and self.db:
                self.cache.flush_session(session_id, self.db)
                if session_id in self.cache._sessions:
                    mem_db = self.cache._sessions.pop(session_id)
                    mem_db.close()
                self.cache._dirty_sessions.discard(session_id)
                self.cache._flushed_offsets.pop(session_id, None)
                self.cache._access_times.pop(session_id, None)
                logger.info("session.close: flushed and evicted session %s from cache", session_id)
            return

        # 8. Session switch: flush previous session and hydrate current
        if topic == "session.switch":
            prev_sid = payload.get("prev_session_id")
            curr_sid = payload.get("session_id")
            if self.mode == "turbo_cache" and self.cache and self.db:
                if prev_sid and self.cache.is_dirty(prev_sid):
                    self.cache.flush_session(prev_sid, self.db)
                if curr_sid:
                    self.cache.get_session_db(curr_sid, main_db=self.db, hydrate=True)
            return

        # 9. Explicit flush command
        if topic == "memory.flush_now":
            session_id = payload.get("session_id")
            if self.mode == "turbo_cache" and self.cache and self.db:
                if session_id:
                    self.cache.flush_session(session_id, self.db)
                else:
                    self.cache.flush_all(self.db)
            return

        # 10. Database clear command
        if topic in ("database.clear", "memory.clear"):
            if self.cache:
                self.cache.clear_all_caches()
            if self.db:
                self.db.clear_all()
            logger.info("memory_sqlite: database and caches wiped to 0")
            return

        # 11. Session delete command
        if topic == "session.delete":
            session_id = payload.get("session_id")
            if session_id:
                if self.cache:
                    self.cache.evict_session(session_id)
                if self.db:
                    self.db.delete_session(session_id)
                logger.info("memory_sqlite: deleted session %s", session_id)
            return


PLUGIN = MemorySqlitePlugin
