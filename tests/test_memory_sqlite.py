"""Unit & Integration Tests for Phase 5 — Memory SQLite Plugin.

Tests:
1. `DatabaseManager`: Schema creation, sessions, messages, memories, FTS5 keyword search,
   vector cosine similarity search, hybrid search, tool invocation audits.
2. `MemorySqlitePlugin`: BasePlugin lifecycle, EventBus pub/sub (`memory.store`,
   `memory.retrieve`, `memory.history`, `user.message`, `tool.result`, `chat.complete`).
3. Cross-session recall: PRD Success Criterion #3 ("What was the path of the bug we discussed yesterday?").
4. Orchestrator auto-recall integration.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from aurora_core.event_bus import EventBus
from aurora_core.plugin_base import PluginContext
from core_logic.orchestrator import Orchestrator
from plugins.memory_sqlite.db import DatabaseManager
from plugins.memory_sqlite.plugin import MemorySqlitePlugin
from plugins.memory_sqlite.vectorizer import MemoryVectorizer, cosine_similarity


# ---------------------------------------------------------------------------
# 1. Vectorizer & Math Tests
# ---------------------------------------------------------------------------

class TestVectorizer:
    def test_vectorizer_norm_and_dimension(self):
        vec = MemoryVectorizer(dimension=384)
        v = vec.embed_text("Aurora v2 long term memory")
        assert len(v) == 384
        norm = sum(x * x for x in v) ** 0.5
        assert pytest.approx(norm, 0.001) == 1.0

    def test_cosine_similarity(self):
        vec = MemoryVectorizer()
        v1 = vec.embed_text("The bug was located in browser.py")
        v2 = vec.embed_text("Where is the bug in browser handler?")
        v3 = vec.embed_text("Recipe for baking sourdough bread")

        sim12 = cosine_similarity(v1, v2)
        sim13 = cosine_similarity(v1, v3)
        assert sim12 > sim13
        assert sim12 > 0.4


# ---------------------------------------------------------------------------
# 2. DatabaseManager Tests
# ---------------------------------------------------------------------------

class TestDatabaseManager:
    def test_init_and_sessions(self, tmp_path: Path):
        db = DatabaseManager(db_path=tmp_path / "test.db")
        db.ensure_session("sess_alpha", title="Alpha Chat")

        sess = db.get_session("sess_alpha")
        assert sess is not None
        assert sess["session_id"] == "sess_alpha"
        assert sess["title"] == "Alpha Chat"
        db.close()

    def test_messages_history(self, tmp_path: Path):
        db = DatabaseManager(db_path=tmp_path / "test.db")
        db.store_message("sess_1", "user", "Hello Aurora")
        db.store_message("sess_1", "assistant", "Hello! How can I help you today?")

        msgs = db.get_messages("sess_1")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello Aurora"
        assert msgs[1]["role"] == "assistant"
        db.close()

    def test_memories_keyword_fts5(self, tmp_path: Path):
        db = DatabaseManager(db_path=tmp_path / "test.db")
        db.store_memory("User prefers dark mode and concise code snippets.", session_id="sess_pref")
        db.store_memory("The server port is configured to 8765.", session_id="sess_conf")

        results = db.search_memories_keyword("dark mode concise")
        assert len(results) >= 1
        assert "dark mode" in results[0]["text"]
        db.close()

    def test_memories_semantic_and_hybrid(self, tmp_path: Path):
        vec = MemoryVectorizer()
        db = DatabaseManager(db_path=tmp_path / "test.db")

        fact1 = "The critical bug was found in plugins/tools_builtin/handlers/browser.py"
        fact2 = "We deployed the web server on port 8765"
        fact3 = "User favorite language is Python"

        db.store_memory(fact1, session_id="sess_yest", embedding=vec.embed_text(fact1))
        db.store_memory(fact2, session_id="sess_yest", embedding=vec.embed_text(fact2))
        db.store_memory(fact3, session_id="sess_pref", embedding=vec.embed_text(fact3))

        query = "What was the path of the bug discussed yesterday?"
        q_emb = vec.embed_text(query)

        hybrid_results = db.search_memories_hybrid(query=query, query_embedding=q_emb, k=2)
        assert len(hybrid_results) > 0
        top = hybrid_results[0]
        assert "browser.py" in top["text"]
        assert "bug" in top["text"]
        db.close()

    def test_tool_invocations_audit(self, tmp_path: Path):
        db = DatabaseManager(db_path=tmp_path / "test.db")
        db.store_tool_invocation(
            session_id="sess_tool",
            tool="calculator",
            call_id="c_1",
            args={"expression": "10 * 10"},
            output={"result": 100},
            ok=True,
        )

        invs = db.get_tool_invocations("sess_tool")
        assert len(invs) == 1
        assert invs[0]["tool"] == "calculator"
        assert invs[0]["ok"] is True
        db.close()


# ---------------------------------------------------------------------------
# 3. MemorySqlitePlugin Integration & EventBus Tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def bus() -> EventBus:
    return EventBus(history_size=64, swallow_subscriber_errors=True)


@pytest.mark.asyncio
async def test_memory_plugin_pubsub_store_and_retrieve(bus: EventBus, tmp_path: Path):
    plugin = MemorySqlitePlugin()
    ctx = PluginContext(
        name="memory_sqlite",
        bus=bus,
        config={"aurora": {"db_path": str(tmp_path / "aurora.db")}},
    )
    await plugin.setup(ctx)

    stored_events: list[dict[str, Any]] = []
    retrieved_events: list[dict[str, Any]] = []

    async def stored_handler(topic: str, payload: dict[str, Any]) -> None:
        stored_events.append(payload)

    async def retrieve_handler(topic: str, payload: dict[str, Any]) -> None:
        retrieved_events.append(payload)

    await bus.subscribe("memory.stored", stored_handler)
    await bus.subscribe("memory.result", retrieve_handler)

    try:
        # 1. Store explicit memory
        await plugin.handle_event(
            "memory.store",
            {
                "call_id": "call_st_1",
                "session_id": "sess_mem1",
                "text": "User is building an AI desktop companion named Aurora v2.",
                "scope": "fact",
            },
        )
        assert len(stored_events) == 1
        assert stored_events[0]["ok"] is True

        # 2. Retrieve memory
        await plugin.handle_event(
            "memory.retrieve",
            {
                "call_id": "call_rt_1",
                "session_id": "sess_mem1",
                "query": "What AI companion is the user building?",
                "k": 2,
            },
        )
        assert len(retrieved_events) == 1
        res = retrieved_events[0]
        assert res["ok"] is True
        assert len(res["memories"]) >= 1
        assert "Aurora v2" in res["memories"][0]["text"]

    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_memory_plugin_user_message_auto_capture(bus: EventBus, tmp_path: Path):
    plugin = MemorySqlitePlugin()
    ctx = PluginContext(
        name="memory_sqlite",
        bus=bus,
        config={"aurora": {"db_path": str(tmp_path / "aurora.db")}},
    )
    await plugin.setup(ctx)
    for topic in plugin.subscribes:
        await bus.subscribe(topic, plugin.handle_event)

    try:
        # User message arrives
        await plugin.handle_event(
            "user.message",
            {"session_id": "sess_auto", "text": "Remember that my secret code is 4242."},
        )

        # Assistant completes turn
        await plugin.handle_event(
            "chat.complete",
            {"session_id": "sess_auto", "text": "I have remembered your secret code."},
        )

        # Retrieve history
        history_events: list[dict[str, Any]] = []

        async def history_handler(topic: str, payload: dict[str, Any]) -> None:
            history_events.append(payload)

        await bus.subscribe("memory.history_result", history_handler)
        await plugin.handle_event(
            "memory.history",
            {"session_id": "sess_auto", "call_id": "hist_1"},
        )

        assert len(history_events) == 1
        msgs = history_events[0]["messages"]
        assert len(msgs) == 2
        assert msgs[0]["content"] == "Remember that my secret code is 4242."
        assert msgs[1]["content"] == "I have remembered your secret code."

    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_cross_session_recall_in_orchestrator(bus: EventBus, tmp_path: Path):
    """PRD F3 Success Criterion #3:

    'What was the path of the bug we discussed yesterday' recalls the correct path.
    """
    memory_plugin = MemorySqlitePlugin()
    ctx = PluginContext(
        name="memory_sqlite",
        bus=bus,
        config={"aurora": {"db_path": str(tmp_path / "aurora.db")}},
    )
    await memory_plugin.setup(ctx)

    # Wire bus subscriptions
    for topic in memory_plugin.subscribes:
        await bus.subscribe(topic, memory_plugin.handle_event)

    orchestrator = Orchestrator(bus=bus)

    # 1. Seed past conversation from yesterday
    yesterday_fact = "The bug path we investigated yesterday was E:/NeuralChat2/plugins/tools_builtin/handlers/browser.py"
    await bus.publish(
        "memory.store",
        {
            "session_id": "sess_yesterday",
            "text": yesterday_fact,
            "scope": "conversation",
        },
    )

    # Let event loop dispatch
    await asyncio.sleep(0.05)

    # 2. Today, user asks in a completely new session
    generated_events: list[dict[str, Any]] = []

    async def generate_handler(topic: str, payload: dict[str, Any]) -> None:
        generated_events.append(payload)

    await bus.subscribe("llm.generate", generate_handler)

    await orchestrator.handle_user_message(
        "user.message",
        {
            "session_id": "sess_today_new",
            "text": "What was the path of the bug we discussed yesterday?",
        },
    )

    try:
        assert len(generated_events) == 1
        sys_message = generated_events[0]["messages"][0]["content"]
        user_message = generated_events[0]["messages"][-1]["content"]

        # Verify memory was recalled and injected into system instructions
        assert "=== LONG-TERM MEMORY & RECALLED FACTS" in sys_message
        assert "plugins/tools_builtin/handlers/browser.py" in sys_message
        assert "sess_yesterday" in sys_message

        # User prompt is clean
        assert user_message == "What was the path of the bug we discussed yesterday?"

    finally:
        await memory_plugin.shutdown()


@pytest.mark.asyncio
async def test_memory_plugin_turbo_cache_mode(bus: EventBus, tmp_path: Path):
    """Test MemorySqlitePlugin in Installation B (Turbo Cache Mode)."""
    db_file = tmp_path / "aurora_turbo.db"
    plugin = MemorySqlitePlugin()
    ctx = PluginContext(
        name="memory_sqlite",
        bus=bus,
        config={
            "aurora": {"db_path": str(db_file)},
            "memory": {
                "mode": "turbo_cache",
                "cache": {"sync_interval_s": 0.1, "max_cached_sessions": 3},
            },
        },
    )
    await plugin.setup(ctx)
    assert plugin.mode == "turbo_cache"
    assert plugin.cache is not None

    for topic in plugin.subscribes:
        await bus.subscribe(topic, plugin.handle_event)

    try:
        # 1. Send user.message in sess_turbo
        await bus.publish(
            "user.message",
            {"session_id": "sess_turbo", "text": "Testing turbo cache memory"},
        )
        await asyncio.sleep(0.02)

        # In-memory cache has the message
        mem_db = plugin.cache.get_session_db("sess_turbo")
        msgs = mem_db.get_messages("sess_turbo")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Testing turbo cache memory"

        # Check periodic flush: wait 0.15s (timer interval is 0.1s in test)
        await asyncio.sleep(0.18)

        # Main DB on disk should now have received the flushed turn!
        main_msgs = plugin.db.get_messages("sess_turbo")
        assert len(main_msgs) == 1
        assert main_msgs[0]["content"] == "Testing turbo cache memory"

        # 2. Add assistant response and test session.close flush
        await bus.publish(
            "chat.complete",
            {"session_id": "sess_turbo", "text": "Assistant response in turbo"},
        )
        await bus.publish(
            "session.close",
            {"session_id": "sess_turbo"},
        )
        await asyncio.sleep(0.05)

        # Main DB should have both turns
        all_main = plugin.db.get_messages("sess_turbo")
        assert len(all_main) == 2
        assert all_main[1]["content"] == "Assistant response in turbo"
        # sess_turbo evicted from cache on close
        assert "sess_turbo" not in plugin.cache._sessions

    finally:
        await plugin.shutdown()


def test_database_clear_all(tmp_path: Path):
    """Test that clear_all wipes all tables, resets autoincrement sequences, and restores to 0."""
    db = DatabaseManager(db_path=tmp_path / "clear_test.db")
    db.ensure_session("sess_to_clear", title="Disposable Chat")
    db.store_message("sess_to_clear", "user", "Message to wipe")
    db.store_message("sess_to_clear", "assistant", "Response to wipe")
    db.store_memory("Fact to wipe", session_id="sess_to_clear")
    db.store_tool_invocation(
        session_id="sess_to_clear",
        tool="terminal",
        call_id="call_99",
        args={"cmd": "dir"},
        output="output",
        ok=True,
    )

    assert len(db.get_messages("sess_to_clear")) == 2
    assert len(db.search_memories_keyword("Fact")) == 1
    assert len(db.get_tool_invocations("sess_to_clear")) == 1

    # Execute clear_all
    res = db.clear_all()
    assert res["ok"] is True

    # Verify all tables back to 0
    assert len(db.get_messages("sess_to_clear")) == 0
    assert db.get_session("sess_to_clear") is None
    assert len(db.search_memories_keyword("Fact")) == 0
    assert len(db.get_tool_invocations("sess_to_clear")) == 0

    # Verify autoincrement resets back to 1
    db.store_message("sess_fresh", "user", "First fresh message")
    msgs = db.get_messages("sess_fresh")
    assert len(msgs) == 1
    assert msgs[0]["id"] == 1
    db.close()


@pytest.mark.asyncio
async def test_memory_plugin_database_clear_event(bus: EventBus, tmp_path: Path):
    """Test that database.clear event flushes and wipes both turbo cache and main DB."""
    plugin = MemorySqlitePlugin()
    ctx = PluginContext(
        name="memory_sqlite",
        bus=bus,
        config={
            "aurora": {"db_path": str(tmp_path / "event_clear.db")},
            "memory": {"mode": "turbo_cache", "cache": {"sync_interval_s": 10.0}},
        },
    )
    await plugin.setup(ctx)
    for topic in plugin.subscribes:
        await bus.subscribe(topic, plugin.handle_event)

    try:
        await bus.publish(
            "user.message",
            {"session_id": "sess_event_clear", "text": "Will be wiped"},
        )
        await asyncio.sleep(0.02)

        # Cache contains the session
        assert "sess_event_clear" in plugin.cache._sessions

        # Send database.clear event
        await bus.publish("database.clear", {})
        await asyncio.sleep(0.02)

        # Cache should be cleared
        assert len(plugin.cache._sessions) == 0
        assert len(plugin.cache._dirty_sessions) == 0

        # Main DB should have 0 messages
        msgs = plugin.db.get_messages("sess_event_clear")
        assert len(msgs) == 0
    finally:
        await plugin.shutdown()


def test_database_list_and_delete_sessions(tmp_path: Path):
    """Test list_sessions auto-titling and delete_session."""
    db = DatabaseManager(db_path=tmp_path / "sessions_test.db")
    db.store_message("sess_history_1", "user", "How to prepare for algorithms exam?")
    db.store_message("sess_history_1", "assistant", "Study trees and graphs thoroughly.")

    db.store_message("sess_history_2", "user", "What is Big O notation?")

    sessions = db.list_sessions()
    assert len(sessions) == 2
    # Verify auto-derived title from user prompt
    titles = {s["session_id"]: s["title"] for s in sessions}
    assert "algorithms exam" in titles["sess_history_1"]
    assert "Big O notation" in titles["sess_history_2"]

    # Verify message counts
    counts = {s["session_id"]: s["message_count"] for s in sessions}
    assert counts["sess_history_1"] == 2
    assert counts["sess_history_2"] == 1

    # Delete session 2
    assert db.delete_session("sess_history_2") is True
    remaining = db.list_sessions()
    assert len(remaining) == 1
    assert remaining[0]["session_id"] == "sess_history_1"
    assert len(db.get_messages("sess_history_2")) == 0
    db.close()



