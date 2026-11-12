"""SQLite Database Manager for Aurora v2 — Phase 5 Memory.

Implements storage layout per architecture.md §6:
Tables: `sessions`, `messages`, `memories` (+ FTS5 index), `tool_invocations`.
Supports hybrid retrieval: SQLite FTS5 BM25 text search + Vector cosine similarity.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugins.memory_sqlite.vectorizer import cosine_similarity

logger = logging.getLogger("aurora.memory.db")


class DatabaseManager:
    """Manages SQLite connection, schema creation, transactions, and hybrid memory queries."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        if str(db_path) == ":memory:":
            self.db_path = ":memory:"
        else:
            p = Path(db_path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(p)

        self._conn = sqlite3.connect(
            self.db_path,
            timeout=15.0,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode
        )
        self._conn.row_factory = sqlite3.Row
        self._init_pragmas()
        self._init_tables()

    def _init_pragmas(self) -> None:
        """Set performance and safety pragmas."""
        cur = self._conn.cursor()
        if self.db_path != ":memory:":
            cur.execute("PRAGMA journal_mode = WAL;")
            cur.execute("PRAGMA wal_autocheckpoint = 100;")
        cur.execute("PRAGMA synchronous = NORMAL;")
        cur.execute("PRAGMA foreign_keys = ON;")

    def _init_tables(self) -> None:
        """Create canonical tables if they don't exist."""
        cur = self._conn.cursor()

        # 1. Sessions table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            """
        )

        # 2. Messages table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_sess ON messages(session_id, timestamp);")

        # 3. Memories table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                text TEXT NOT NULL,
                scope TEXT DEFAULT 'conversation',
                embedding TEXT DEFAULT '[]',
                timestamp TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_sess ON memories(session_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope);")

        # 4. FTS5 Virtual Table for full-text search on memories
        try:
            cur.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    text,
                    content='memories',
                    content_rowid='id'
                );
                """
            )
            # Sync triggers between memories and memories_fts
            cur.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
                END;
                """
            )
            cur.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text);
                END;
                """
            )
            cur.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text);
                    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
                END;
                """
            )
        except sqlite3.OperationalError as exc:
            logger.warning("SQLite FTS5 virtual table setup skipped: %s", exc)

        # 5. Tool Invocations table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_invocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                call_id TEXT,
                tool TEXT NOT NULL,
                args TEXT DEFAULT '{}',
                output TEXT DEFAULT '',
                ok INTEGER DEFAULT 1,
                timestamp TEXT NOT NULL
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tool_inv_sess ON tool_invocations(session_id);")

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # -------------------------------------------------------------------
    # Sessions
    # -------------------------------------------------------------------

    def ensure_session(self, session_id: str, title: str = "", metadata: dict[str, Any] | None = None) -> None:
        """Create or update session record."""
        now = self._now_iso()
        meta_json = json.dumps(metadata or {})
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO sessions (session_id, title, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                title = CASE
                    WHEN sessions.title != '' THEN sessions.title
                    WHEN excluded.title != '' THEN excluded.title
                    ELSE sessions.title
                END;
            """,
            (session_id, title, now, now, meta_json),
        )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Fetch session by ID."""
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "session_id": row["session_id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": json.loads(row["metadata"] or "{}"),
        }

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent sessions ordered by updated_at descending with message counts and previews."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT s.session_id, s.title, s.created_at, s.updated_at, s.metadata,
                   COUNT(m.id) as message_count,
                   (SELECT content FROM messages WHERE session_id = s.session_id AND role = 'user' ORDER BY id ASC LIMIT 1) as first_user_message,
                   (SELECT content FROM messages WHERE session_id = s.session_id ORDER BY id DESC LIMIT 1) as last_message
            FROM sessions s
            LEFT JOIN messages m ON s.session_id = m.session_id
            GROUP BY s.session_id
            HAVING message_count > 0 OR s.title != ''
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        results = []
        for r in rows:
            title = (r["title"] or "").strip()
            if not title and r["first_user_message"]:
                first = r["first_user_message"].strip().splitlines()[0]
                title = first[:38] + ("…" if len(first) > 38 else "")
            elif not title:
                title = f"Chat {r['session_id'][:8]}"

            preview = (r["last_message"] or r["first_user_message"] or "").strip().splitlines()[0] if (r["last_message"] or r["first_user_message"]) else ""
            preview = preview[:60] + ("…" if len(preview) > 60 else "")

            results.append({
                "session_id": r["session_id"],
                "title": title,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "metadata": json.loads(r["metadata"] or "{}"),
                "message_count": r["message_count"],
                "preview": preview,
            })
        return results

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its messages, memories, and tool invocations."""
        cur = self._conn.cursor()
        cur.execute("PRAGMA foreign_keys = OFF;")
        cur.execute("DELETE FROM memories WHERE session_id = ?;", (session_id,))
        cur.execute("DELETE FROM tool_invocations WHERE session_id = ?;", (session_id,))
        cur.execute("DELETE FROM messages WHERE session_id = ?;", (session_id,))
        cur.execute("DELETE FROM sessions WHERE session_id = ?;", (session_id,))
        cur.execute("PRAGMA foreign_keys = ON;")
        return True

    # -------------------------------------------------------------------
    # Messages
    # -------------------------------------------------------------------

    def store_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Store a chat message in the session history."""
        title = ""
        if role == "user":
            first_line = content.strip().splitlines()[0] if content.strip() else ""
            if first_line:
                title = first_line[:38] + ("…" if len(first_line) > 38 else "")
        self.ensure_session(session_id, title=title)
        now = self._now_iso()
        meta_json = json.dumps(metadata or {})
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO messages (session_id, role, content, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, content, now, meta_json),
        )
        msg_id = int(cur.lastrowid)
        if self.db_path != ":memory:":
            try:
                cur.execute("PRAGMA wal_checkpoint(PASSIVE);")
            except Exception:
                pass
        return msg_id

    def get_messages(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent chat history for a session."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id, session_id, role, content, timestamp, metadata
            FROM messages
            WHERE session_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (session_id, limit),
        )
        rows = cur.fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "role": r["role"],
                "content": r["content"],
                "timestamp": r["timestamp"],
                "metadata": json.loads(r["metadata"] or "{}"),
            }
            for r in rows
        ]

    # -------------------------------------------------------------------
    # Memories & Hybrid Search
    # -------------------------------------------------------------------

    def store_memory(
        self,
        text: str,
        session_id: str | None = None,
        scope: str = "conversation",
        embedding: list[float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Store a factual or conversational memory turn."""
        if session_id:
            self.ensure_session(session_id)
        now = self._now_iso()
        emb_json = json.dumps(embedding or [])
        meta_json = json.dumps(metadata or {})
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO memories (session_id, text, scope, embedding, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, text.strip(), scope, emb_json, now, meta_json),
        )
        return int(cur.lastrowid)

    def search_memories_keyword(
        self,
        query: str,
        session_id: str | None = None,
        cross_session: bool = True,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Full-text search (BM25) over memories using FTS5."""
        if not query or not query.strip():
            return []

        # Sanitize query for FTS5 (escape quotes, wrap words)
        clean_terms = [t for t in query.replace('"', "").replace("'", "").split() if t.isalnum()]
        if not clean_terms:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in clean_terms)

        cur = self._conn.cursor()
        try:
            if not cross_session and session_id:
                cur.execute(
                    """
                    SELECT m.id, m.session_id, m.text, m.scope, m.timestamp, m.metadata,
                           rank as bm25_rank
                    FROM memories_fts f
                    JOIN memories m ON f.rowid = m.id
                    WHERE memories_fts MATCH ? AND (m.session_id = ? OR m.session_id IS NULL)
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, session_id, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT m.id, m.session_id, m.text, m.scope, m.timestamp, m.metadata,
                           rank as bm25_rank
                    FROM memories_fts f
                    JOIN memories m ON f.rowid = m.id
                    WHERE memories_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                )
            rows = cur.fetchall()
            return [
                {
                    "id": r["id"],
                    "session_id": r["session_id"],
                    "text": r["text"],
                    "scope": r["scope"],
                    "timestamp": r["timestamp"],
                    "metadata": json.loads(r["metadata"] or "{}"),
                    "score": round(abs(r["bm25_rank"]), 4) if "bm25_rank" in r.keys() else 1.0,
                    "match_type": "keyword",
                }
                for r in rows
            ]
        except sqlite3.OperationalError as exc:
            logger.debug("FTS5 match failed, falling back to LIKE: %s", exc)
            return self._search_memories_like(query, session_id if not cross_session else None, limit)

    def _search_memories_like(self, query: str, session_id: str | None, limit: int) -> list[dict[str, Any]]:
        """Fallback keyword search using LIKE."""
        terms = [t for t in query.split() if len(t) > 2]
        if not terms:
            return []
        like_expr = "%" + "%".join(terms) + "%"
        cur = self._conn.cursor()
        if session_id:
            cur.execute(
                """
                SELECT id, session_id, text, scope, timestamp, metadata
                FROM memories
                WHERE text LIKE ? AND (session_id = ? OR session_id IS NULL)
                ORDER BY id DESC LIMIT ?
                """,
                (like_expr, session_id, limit),
            )
        else:
            cur.execute(
                "SELECT id, session_id, text, scope, timestamp, metadata FROM memories WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                (like_expr, limit),
            )
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "text": r["text"],
                "scope": r["scope"],
                "timestamp": r["timestamp"],
                "metadata": json.loads(r["metadata"] or "{}"),
                "score": 0.5,
                "match_type": "like",
            }
            for r in cur.fetchall()
        ]

    def search_memories_semantic(
        self,
        query_embedding: list[float],
        session_id: str | None = None,
        cross_session: bool = True,
        k: int = 5,
        min_score: float = 0.05,
    ) -> list[dict[str, Any]]:
        """Vector cosine similarity search over stored memory embeddings."""
        if not query_embedding:
            return []

        cur = self._conn.cursor()
        if not cross_session and session_id:
            cur.execute(
                """
                SELECT id, session_id, text, scope, embedding, timestamp, metadata
                FROM memories
                WHERE (session_id = ? OR session_id IS NULL OR session_id = 'global')
                ORDER BY id DESC LIMIT 500
                """,
                (session_id,),
            )
        else:
            cur.execute(
                """
                SELECT id, session_id, text, scope, embedding, timestamp, metadata
                FROM memories
                ORDER BY id DESC LIMIT 500
                """
            )

        rows = cur.fetchall()
        scored: list[tuple[float, dict[str, Any]]] = []

        for r in rows:
            emb_str = r["embedding"]
            if not emb_str or emb_str == "[]":
                continue
            try:
                emb = json.loads(emb_str)
                score = cosine_similarity(query_embedding, emb)
                if score >= min_score:
                    scored.append((
                        score,
                        {
                            "id": r["id"],
                            "session_id": r["session_id"],
                            "text": r["text"],
                            "scope": r["scope"],
                            "timestamp": r["timestamp"],
                            "metadata": json.loads(r["metadata"] or "{}"),
                            "score": round(score, 4),
                            "match_type": "semantic",
                        },
                    ))
            except Exception:
                continue

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:k]]

    def search_memories_hybrid(
        self,
        query: str,
        query_embedding: list[float] | None = None,
        session_id: str | None = None,
        cross_session: bool = True,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """Hybrid search combining keyword FTS5 and semantic cosine similarity."""
        results: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        # 1. Semantic vector search
        if query_embedding:
            sem_results = self.search_memories_semantic(
                query_embedding,
                session_id=session_id,
                cross_session=cross_session,
                k=k,
            )
            for r in sem_results:
                seen_ids.add(r["id"])
                results.append(r)

        # 2. Keyword FTS5 search for exact names, paths, numbers
        kw_results = self.search_memories_keyword(
            query,
            session_id=session_id,
            cross_session=cross_session,
            limit=k,
        )
        for r in kw_results:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                results.append(r)

        return results[:k]

    # -------------------------------------------------------------------
    # Tool Invocations
    # -------------------------------------------------------------------

    def store_tool_invocation(
        self,
        session_id: str,
        tool: str,
        call_id: str = "",
        args: dict[str, Any] | None = None,
        output: Any = "",
        ok: bool = True,
    ) -> int:
        """Log a tool execution into audit history."""
        now = self._now_iso()
        args_json = json.dumps(args or {})
        out_str = str(output)[:4000]  # Cap audit output length
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO tool_invocations (session_id, call_id, tool, args, output, ok, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, call_id, tool, args_json, out_str, 1 if ok else 0, now),
        )
        return int(cur.lastrowid)

    def get_tool_invocations(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve recent tool invocations for a session."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id, session_id, call_id, tool, args, output, ok, timestamp
            FROM tool_invocations
            WHERE session_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (session_id, limit),
        )
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "call_id": r["call_id"],
                "tool": r["tool"],
                "args": json.loads(r["args"] or "{}"),
                "output": r["output"],
                "ok": bool(r["ok"]),
                "timestamp": r["timestamp"],
            }
            for r in cur.fetchall()
        ]

    def clear_all(self) -> dict[str, Any]:
        """Delete all records from all tables, reset auto-increment sequences, and vacuum."""
        cur = self._conn.cursor()
        cur.execute("PRAGMA foreign_keys = OFF;")
        cur.execute("DELETE FROM tool_invocations;")
        cur.execute("DELETE FROM memories;")
        try:
            cur.execute("DELETE FROM memories_fts;")
        except sqlite3.OperationalError:
            pass
        cur.execute("DELETE FROM messages;")
        cur.execute("DELETE FROM sessions;")
        try:
            cur.execute("DELETE FROM sqlite_sequence;")
        except sqlite3.OperationalError:
            pass
        cur.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            try:
                cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                cur.execute("VACUUM;")
            except Exception as exc:
                logger.warning("Vacuum after clear failed: %s", exc)
        return {"ok": True, "message": "SQLite database tables cleared and vacuumed."}

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            try:
                if self.db_path != ":memory:":
                    self._conn.cursor().execute("PRAGMA wal_checkpoint(TRUNCATE);")
                self._conn.close()
            except Exception:
                pass
            self._conn = None
