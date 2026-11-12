"""Session Cache Manager — Installation B (Turbo / In-Memory RAM Cache Mode).

Provides Redis-style in-memory session isolation for active chat turns:
1. Each active chat session maintains an isolated `sqlite3 :memory:` instance.
2. Textual context (messages, conversation memories, AST summaries) is read/written
   in RAM with sub-millisecond latency. Heavy files bypass this cache.
3. Tracks dirty sessions and offsets to allow incremental, idempotent batch flushes
   into the main persistent SQLite database (`data/aurora.db`).
4. LRU Eviction: Retains at most `max_cached_sessions` (default: 5) in memory.
   When capacity is reached, the least-recently-used session is flushed to disk
   and evicted from RAM.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any

from plugins.memory_sqlite.db import DatabaseManager

logger = logging.getLogger("aurora.memory.cache")


class SessionCacheManager:
    """Manages active in-memory session databases with LRU eviction and write-through flush."""

    def __init__(self, max_cached_sessions: int = 5) -> None:
        self.max_cached_sessions = max(1, max_cached_sessions)
        # session_id -> DatabaseManager (in-memory)
        self._sessions: OrderedDict[str, DatabaseManager] = OrderedDict()
        # session_id -> set of dirty entities or boolean
        self._dirty_sessions: set[str] = set()
        # session_id -> {"msg_id": int, "mem_id": int, "tool_id": int}
        self._flushed_offsets: dict[str, dict[str, int]] = {}
        # session_id -> last_access_timestamp
        self._access_times: dict[str, float] = {}

    def get_session_db(
        self,
        session_id: str,
        main_db: DatabaseManager | None = None,
        hydrate: bool = True,
    ) -> DatabaseManager:
        """Get or initialize in-memory database for a session.

        Updates LRU order and optionally hydrates initial recent turns from main_db.
        """
        now = time.time()
        self._access_times[session_id] = now

        if session_id in self._sessions:
            # Move to end of OrderedDict (most recently used)
            self._sessions.move_to_end(session_id)
            return self._sessions[session_id]

        # Enforce LRU eviction if at capacity
        if len(self._sessions) >= self.max_cached_sessions:
            self.evict_lru(main_db=main_db)

        # Create new in-memory SQLite database
        mem_db = DatabaseManager(db_path=":memory:")
        self._sessions[session_id] = mem_db
        self._flushed_offsets[session_id] = {"msg_id": 0, "mem_id": 0, "tool_id": 0}

        # Hydrate existing turns from persistent main database if available
        if hydrate and main_db:
            try:
                self._hydrate_session(session_id, mem_db, main_db)
            except Exception as exc:
                logger.warning("Failed to hydrate session %s into cache: %s", session_id, exc)

        return mem_db

    def _hydrate_session(
        self,
        session_id: str,
        mem_db: DatabaseManager,
        main_db: DatabaseManager,
        limit: int = 50,
    ) -> None:
        """Hydrate recent session context from main DB into in-memory store."""
        # 1. Session record
        sess_info = main_db.get_session(session_id)
        if sess_info:
            mem_db.ensure_session(
                session_id=session_id,
                title=sess_info.get("title", ""),
                metadata=sess_info.get("metadata", {}),
            )

        # 2. Recent messages
        messages = main_db.get_messages(session_id=session_id, limit=limit)
        cur_mem = mem_db._conn.cursor()
        max_id = 0
        for m in messages:
            row_id = mem_db.store_message(
                session_id=session_id,
                role=m["role"],
                content=m["content"],
                metadata=m.get("metadata"),
            )
            max_id = max(max_id, row_id)

        # Mark hydrated records as already flushed so we don't duplicate them back to disk
        self._flushed_offsets[session_id]["msg_id"] = max_id

    def mark_dirty(self, session_id: str) -> None:
        """Mark session as containing uncommitted in-memory writes."""
        self._dirty_sessions.add(session_id)
        self._access_times[session_id] = time.time()
        if session_id in self._sessions:
            self._sessions.move_to_end(session_id)

    def is_dirty(self, session_id: str) -> bool:
        """Check if a session has uncommitted in-memory records."""
        return session_id in self._dirty_sessions

    def get_dirty_sessions(self) -> list[str]:
        """Return list of session IDs with uncommitted in-memory writes."""
        return [sid for sid in self._dirty_sessions if sid in self._sessions]

    def flush_session(self, session_id: str, main_db: DatabaseManager) -> int:
        """Batch write uncommitted in-memory turns to persistent main database.

        Returns total number of records (messages + memories + tool logs) written.
        """
        if session_id not in self._sessions:
            self._dirty_sessions.discard(session_id)
            return 0

        mem_db = self._sessions[session_id]
        offsets = self._flushed_offsets.setdefault(
            session_id, {"msg_id": 0, "mem_id": 0, "tool_id": 0}
        )

        cur_mem = mem_db._conn.cursor()
        cur_main = main_db._conn.cursor()

        # Fetch records newer than the last flushed offset
        cur_mem.execute(
            """
            SELECT id, session_id, role, content, timestamp, metadata
            FROM messages
            WHERE id > ?
            ORDER BY id ASC
            """,
            (offsets["msg_id"],),
        )
        new_msgs = cur_mem.fetchall()

        cur_mem.execute(
            """
            SELECT id, session_id, text, scope, embedding, timestamp, metadata
            FROM memories
            WHERE id > ?
            ORDER BY id ASC
            """,
            (offsets["mem_id"],),
        )
        new_mems = cur_mem.fetchall()

        cur_mem.execute(
            """
            SELECT id, session_id, call_id, tool, args, output, ok, timestamp
            FROM tool_invocations
            WHERE id > ?
            ORDER BY id ASC
            """,
            (offsets["tool_id"],),
        )
        new_tools = cur_mem.fetchall()

        total_new = len(new_msgs) + len(new_mems) + len(new_tools)
        if total_new == 0:
            self._dirty_sessions.discard(session_id)
            return 0

        # Execute atomic write-through transaction into main DB
        main_conn = main_db._conn
        main_conn.execute("BEGIN IMMEDIATE;")
        try:
            # 1. Update session record
            main_db.ensure_session(session_id)

            # 2. Insert new messages
            for m in new_msgs:
                cur_main.execute(
                    """
                    INSERT INTO messages (session_id, role, content, timestamp, metadata)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (m["session_id"], m["role"], m["content"], m["timestamp"], m["metadata"]),
                )

            # 3. Insert new memories
            for mem in new_mems:
                cur_main.execute(
                    """
                    INSERT INTO memories (session_id, text, scope, embedding, timestamp, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mem["session_id"],
                        mem["text"],
                        mem["scope"],
                        mem["embedding"],
                        mem["timestamp"],
                        mem["metadata"],
                    ),
                )

            # 4. Insert new tool invocations
            for t in new_tools:
                cur_main.execute(
                    """
                    INSERT INTO tool_invocations (session_id, call_id, tool, args, output, ok, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        t["session_id"],
                        t["call_id"],
                        t["tool"],
                        t["args"],
                        t["output"],
                        t["ok"],
                        t["timestamp"],
                    ),
                )

            main_conn.execute("COMMIT;")
        except Exception as exc:
            main_conn.execute("ROLLBACK;")
            logger.error("Failed to flush session %s to main database: %s", session_id, exc)
            raise

        # Update offsets on success
        if new_msgs:
            offsets["msg_id"] = new_msgs[-1]["id"]
        if new_mems:
            offsets["mem_id"] = new_mems[-1]["id"]
        if new_tools:
            offsets["tool_id"] = new_tools[-1]["id"]

        self._dirty_sessions.discard(session_id)
        logger.info(
            "Flushed session %s to disk: %d messages, %d memories, %d tool calls",
            session_id,
            len(new_msgs),
            len(new_mems),
            len(new_tools),
        )
        return total_new

    def flush_all(self, main_db: DatabaseManager) -> dict[str, int]:
        """Flush all dirty cached sessions to main persistent database."""
        results: dict[str, int] = {}
        dirty_list = list(self._dirty_sessions)
        for sid in dirty_list:
            try:
                count = self.flush_session(sid, main_db)
                results[sid] = count
            except Exception as exc:
                logger.error("Error during flush_all for session %s: %s", sid, exc)
        return results

    def evict_lru(self, main_db: DatabaseManager | None = None) -> str | None:
        """Evict the least recently used session from memory cache.

        Flushes to main_db first if dirty.
        """
        if not self._sessions:
            return None

        # Oldest key in OrderedDict is the LRU
        lru_session_id = next(iter(self._sessions))
        logger.info("Evicting LRU session from in-memory cache: %s", lru_session_id)

        # Flush dirty records before eviction
        if main_db and self.is_dirty(lru_session_id):
            try:
                self.flush_session(lru_session_id, main_db)
            except Exception as exc:
                logger.error("Failed to flush LRU session %s before eviction: %s", lru_session_id, exc)

        # Close and evict in-memory database
        mem_db = self._sessions.pop(lru_session_id)
        mem_db.close()
        self._dirty_sessions.discard(lru_session_id)
        self._flushed_offsets.pop(lru_session_id, None)
        self._access_times.pop(lru_session_id, None)

        return lru_session_id

    def evict_session(self, session_id: str) -> None:
        """Evict a specific session from cache without flushing to disk."""
        if session_id in self._sessions:
            mem_db = self._sessions.pop(session_id)
            try:
                mem_db.close()
            except Exception:
                pass
        self._dirty_sessions.discard(session_id)
        self._flushed_offsets.pop(session_id, None)
        self._access_times.pop(session_id, None)

    def clear_all_caches(self) -> None:
        """Close and discard all in-memory databases immediately without flushing to disk."""
        for sid, mem_db in list(self._sessions.items()):
            try:
                mem_db.close()
            except Exception:
                pass
        self._sessions.clear()
        self._dirty_sessions.clear()
        self._flushed_offsets.clear()
        self._access_times.clear()

    def close_all(self, main_db: DatabaseManager | None = None) -> None:
        """Flush and close all in-memory databases on shutdown."""
        if main_db:
            self.flush_all(main_db)

        for sid, mem_db in list(self._sessions.items()):
            try:
                mem_db.close()
            except Exception:
                pass
        self._sessions.clear()
        self._dirty_sessions.clear()
        self._flushed_offsets.clear()
        self._access_times.clear()
