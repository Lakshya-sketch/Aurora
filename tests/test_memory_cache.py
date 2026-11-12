"""Unit tests for SessionCacheManager — Installation B (Turbo / In-Memory RAM Cache Mode)."""

from __future__ import annotations

from pathlib import Path
import pytest

from plugins.memory_sqlite.cache_manager import SessionCacheManager
from plugins.memory_sqlite.db import DatabaseManager


def test_cache_store_and_retrieve_in_memory(tmp_path: Path):
    main_db = DatabaseManager(db_path=tmp_path / "main.db")
    cache = SessionCacheManager(max_cached_sessions=5)

    mem_db = cache.get_session_db("sess_1", main_db=main_db)
    mem_db.store_message("sess_1", "user", "Hello from in-memory cache!")
    cache.mark_dirty("sess_1")

    # In-memory retrieval works immediately
    msgs = mem_db.get_messages("sess_1")
    assert len(msgs) == 1
    assert msgs[0]["content"] == "Hello from in-memory cache!"

    # Main database on disk should have ZERO messages yet (Option 1 pure batch timer)
    assert len(main_db.get_messages("sess_1")) == 0
    assert cache.is_dirty("sess_1") is True


def test_cache_flush_to_main_db(tmp_path: Path):
    main_db = DatabaseManager(db_path=tmp_path / "main.db")
    cache = SessionCacheManager(max_cached_sessions=5)

    mem_db = cache.get_session_db("sess_flush", main_db=main_db)
    mem_db.store_message("sess_flush", "user", "Turn 1 question")
    mem_db.store_message("sess_flush", "assistant", "Turn 1 answer")
    mem_db.store_memory(text="User likes Python", session_id="sess_flush", scope="conversation")
    mem_db.store_tool_invocation(session_id="sess_flush", tool="calculator", ok=True)
    cache.mark_dirty("sess_flush")

    assert len(main_db.get_messages("sess_flush")) == 0

    # Flush session to main database
    flushed_count = cache.flush_session("sess_flush", main_db)
    assert flushed_count == 4  # 2 msgs + 1 memory + 1 tool call
    assert cache.is_dirty("sess_flush") is False

    # Main DB should now contain the records
    main_msgs = main_db.get_messages("sess_flush")
    assert len(main_msgs) == 2
    assert main_msgs[0]["content"] == "Turn 1 question"
    assert main_msgs[1]["content"] == "Turn 1 answer"

    tool_invs = main_db.get_tool_invocations("sess_flush")
    assert len(tool_invs) == 1
    assert tool_invs[0]["tool"] == "calculator"


def test_cache_incremental_flush_no_duplicates(tmp_path: Path):
    main_db = DatabaseManager(db_path=tmp_path / "main.db")
    cache = SessionCacheManager(max_cached_sessions=5)

    mem_db = cache.get_session_db("sess_inc", main_db=main_db)
    mem_db.store_message("sess_inc", "user", "Turn 1")
    cache.mark_dirty("sess_inc")
    cache.flush_session("sess_inc", main_db)
    assert len(main_db.get_messages("sess_inc")) == 1

    # Turn 2 in RAM
    mem_db.store_message("sess_inc", "assistant", "Turn 2")
    cache.mark_dirty("sess_inc")

    # Flush again
    flushed_count = cache.flush_session("sess_inc", main_db)
    assert flushed_count == 1  # Only the new turn

    # Total in main DB should be exactly 2 (no duplicates of Turn 1)
    main_msgs = main_db.get_messages("sess_inc")
    assert len(main_msgs) == 2
    assert [m["content"] for m in main_msgs] == ["Turn 1", "Turn 2"]


def test_cache_lru_eviction(tmp_path: Path):
    main_db = DatabaseManager(db_path=tmp_path / "main.db")
    # Capacity is 3 sessions
    cache = SessionCacheManager(max_cached_sessions=3)

    # Open 3 sessions
    for i in range(1, 4):
        sid = f"sess_{i}"
        db = cache.get_session_db(sid, main_db=main_db)
        db.store_message(sid, "user", f"Message in {sid}")
        cache.mark_dirty(sid)

    assert len(cache._sessions) == 3
    assert "sess_1" in cache._sessions

    # Open 4th session -> triggers LRU eviction of sess_1
    db4 = cache.get_session_db("sess_4", main_db=main_db)
    db4.store_message("sess_4", "user", "Message in sess_4")
    cache.mark_dirty("sess_4")

    # sess_1 should be evicted from RAM
    assert len(cache._sessions) == 3
    assert "sess_1" not in cache._sessions
    assert "sess_4" in cache._sessions

    # But sess_1 was dirty, so it was flushed to main_db on eviction!
    s1_msgs = main_db.get_messages("sess_1")
    assert len(s1_msgs) == 1
    assert s1_msgs[0]["content"] == "Message in sess_1"


def test_cache_hydration_from_main_db(tmp_path: Path):
    main_db = DatabaseManager(db_path=tmp_path / "main.db")
    # Simulate past session stored in main DB
    main_db.store_message("sess_past", "user", "Historical turn 1")
    main_db.store_message("sess_past", "assistant", "Historical turn 2")

    cache = SessionCacheManager(max_cached_sessions=5)
    # Open session in cache -> hydrates from main_db
    mem_db = cache.get_session_db("sess_past", main_db=main_db, hydrate=True)

    cache_msgs = mem_db.get_messages("sess_past")
    assert len(cache_msgs) == 2
    assert cache_msgs[0]["content"] == "Historical turn 1"

    # Now add new turn
    mem_db.store_message("sess_past", "user", "New turn 3")
    cache.mark_dirty("sess_past")

    # Flush session -> should ONLY write turn 3 to main DB
    flushed = cache.flush_session("sess_past", main_db)
    assert flushed == 1
    assert len(main_db.get_messages("sess_past")) == 3
