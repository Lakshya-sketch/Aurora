"""Transport layer: HTTP + WebSocket for the Electron UI.

Phase 1 scope (per ``implementation_plan.md``):

  * Quart app with ``/health``, ``/plugins``, ``/config`` HTTP routes.
  * WebSocket route ``/stream`` that bridges the kernel's
    ``EventBus`` to the UI in both directions:
      - UI -> Core: ``{"type": "publish", "topic": ..., "payload": ...}``
      - Core -> UI: ``{"type": "event", "topic": ..., "payload": ...}``
  * Typed envelopes in ``ipc.py`` shared with the renderer.

Quart is async ASGI. We run it under Hypercorn (also installed in
Phase 0) so a single process serves HTTP and WebSocket.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from quart import Quart, Response, jsonify, websocket
from quart.typing import TestClientProtocol

from aurora_core import __version__
from aurora_core.config import get
from aurora_core.event_bus import EventBus
from aurora_core.kernel import Kernel

from .ipc import envelope_event, envelope_publish, parse_envelope

logger = logging.getLogger("aurora.transport")

# A single ASGI app lives at module level so Hypercorn / tests can
# both import it. Build via ``create_app(kernel)`` at startup time;
# the global ``app`` is the default-quart instance Quart expects
# when run with ``quart run`` for dev. In production we use
# ``hypercorn aurora_core.transport.http:app`` after wiring.
app = Quart(__name__)

# Set by ``wire_app`` before the server starts.
_kernel: Kernel | None = None


# ---------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------


def wire_app(kernel: Kernel) -> None:
    """Attach a running kernel to the Quart app + bus bridge.

    Must be called before the server starts serving requests.
    """
    global _kernel
    _kernel = kernel

    # Wire Orchestrator agent engine to subscribe to user.message
    from core_logic.orchestrator import Orchestrator
    orchestrator = Orchestrator(kernel.bus)
    asyncio.create_task(kernel.bus.subscribe("user.message", orchestrator.handle_user_message))
    logger.info("transport: wired Orchestrator to 'user.message' topic")


def get_kernel() -> Kernel:
    if _kernel is None:
        raise RuntimeError("transport app not wired (wire_app() not called)")
    return _kernel


# ---------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------


@app.get("/health")
async def health() -> Response:
    """Liveness + plugin status. Used by the UI header and by curl."""
    k = get_kernel()
    return jsonify(
        {
            "up": True,
            "version": __version__,
            "plugins": [p["name"] for p in k.summary()["plugins"]],
            "subscribers": k.bus.subscriber_count(),
            "topics": k.bus.topics(),
        }
    )


@app.get("/plugins")
async def plugins() -> Response:
    """Detailed plugin info — capabilities, requires, subscribes."""
    k = get_kernel()
    return jsonify(k.summary()["plugins"])


@app.get("/config")
async def config() -> Response:
    """Read-only view of the resolved config."""
    k = get_kernel()
    return jsonify(k.cfg)


def _format_size(size_bytes: int) -> str:
    """Format bytes into a human-friendly string (B, KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _get_database_storage_info(k: Kernel) -> dict[str, Any]:
    """Calculate disk space used by SQLite database and Vector RAG store."""
    from pathlib import Path
    import sqlite3

    data_dir_str = k.cfg.get("aurora", {}).get("data_dir", "data")
    data_dir = Path(data_dir_str)

    db_path_str = k.cfg.get("aurora", {}).get("db_path") or k.cfg.get("memory", {}).get("db_path")
    if db_path_str:
        db_path = Path(db_path_str)
    else:
        db_path = data_dir / "aurora.db"

    # SQLite file sizes (main db + wal + shm)
    sqlite_bytes = 0
    if db_path.exists():
        try:
            sqlite_bytes += db_path.stat().st_size
        except OSError:
            pass

    wal_path = Path(str(db_path) + "-wal")
    if wal_path.exists():
        try:
            sqlite_bytes += wal_path.stat().st_size
        except OSError:
            pass

    shm_path = Path(str(db_path) + "-shm")
    if shm_path.exists():
        try:
            sqlite_bytes += shm_path.stat().st_size
        except OSError:
            pass

    # Vector store directory
    chroma_dir_str = k.cfg.get("rag", {}).get("chroma_dir")
    if chroma_dir_str:
        chroma_dir = Path(chroma_dir_str)
    else:
        chroma_dir = data_dir / "chroma"

    vector_bytes = 0
    if chroma_dir.exists() and chroma_dir.is_dir():
        for file in chroma_dir.rglob("*"):
            if file.is_file():
                try:
                    vector_bytes += file.stat().st_size
                except OSError:
                    pass

    total_bytes = sqlite_bytes + vector_bytes

    # Table counts if SQLite is readable
    messages_count = 0
    memories_count = 0
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path), timeout=1.0)
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM messages;")
            messages_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM memories;")
            memories_count = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass

    # Baseline 100 MB for progress bar rendering
    baseline_mb = 100.0
    percent_used = round(min(100.0, (total_bytes / (baseline_mb * 1024 * 1024)) * 100.0), 2)

    # Memory plugin active mode
    memory_mode = "standard"
    cached_sessions_count = 0
    for p in k.plugins:
        if getattr(p, "name", "") == "memory_sqlite":
            memory_mode = getattr(p, "mode", "standard")
            cache_obj = getattr(p, "cache", None)
            if cache_obj:
                cached_sessions_count = len(getattr(cache_obj, "_sessions", {}))
            break

    return {
        "total_bytes": total_bytes,
        "total_human": _format_size(total_bytes),
        "sqlite_bytes": sqlite_bytes,
        "sqlite_human": _format_size(sqlite_bytes),
        "vector_bytes": vector_bytes,
        "vector_human": _format_size(vector_bytes),
        "percent_used": percent_used,
        "messages_count": messages_count,
        "memories_count": memories_count,
        "baseline_mb": baseline_mb,
        "memory_mode": memory_mode,
        "cached_sessions": cached_sessions_count,
    }


@app.get("/stats")
async def stats() -> Response:
    """Runtime usage metrics: active model, tokens spent, and database storage info."""
    k = get_kernel()
    active_model = "None"
    vlm_model = "None"
    total_tokens = 0
    installed_models = []

    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "llm_ollama":
            active_model = getattr(plugin, "active_model", "Unknown")
            vlm_model = getattr(plugin, "vlm_model", "qwen3-vl:4b-instruct-q4_K_M")
            total_tokens = getattr(plugin, "total_tokens_spent", 0)
            installed_models = getattr(plugin, "installed_models", [])
            break

    db_info = _get_database_storage_info(k)

    image_gen_model = "sdxl-lightning"
    available_image_models = []
    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "tools_builtin":
            if hasattr(plugin, "get_image_model"):
                image_gen_model = plugin.get_image_model()
            if hasattr(plugin, "get_available_image_models"):
                available_image_models = plugin.get_available_image_models()
            break

    return jsonify({
        "active_model": active_model,
        "vlm_model": vlm_model,
        "image_gen_model": image_gen_model,
        "available_image_models": available_image_models,
        "total_tokens": total_tokens,
        "installed_models": installed_models,
        "database": db_info,
    })


@app.get("/database/stats")
async def database_stats() -> Response:
    """Get standalone database storage stats."""
    k = get_kernel()
    return jsonify(_get_database_storage_info(k))


@app.route("/database/clear", methods=["POST", "DELETE"])
@app.route("/memory/clear", methods=["POST", "DELETE"])
async def clear_database() -> Response:
    """Clean the entire database back to 0:
    - SQLite database tables: sessions, messages, memories, tool_invocations
    - In-memory turbo cache sessions
    - RAG Chroma vector store records & persistent file
    - Active memory inspector buffers
    """
    k = get_kernel()

    # 1. Clear SQLite tables & Turbo Cache in memory_sqlite
    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "memory_sqlite":
            if getattr(plugin, "cache", None):
                plugin.cache.clear_all_caches()
            if getattr(plugin, "db", None):
                plugin.db.clear_all()

    # 2. Clear RAG Chroma vector store
    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "rag_chroma":
            if getattr(plugin, "store", None):
                plugin.store.clear()

    # 3. Clear in-memory inspector buffers
    try:
        from core_logic.memory_inspector import clear_memory_inspector
        clear_memory_inspector()
    except Exception:
        pass

    # 4. Notify any listeners on the EventBus
    try:
        await k.bus.publish("database.cleared", {})
    except Exception:
        pass

    # 5. Return updated storage stats showing everything reset to 0
    db_info = _get_database_storage_info(k)
    return jsonify({
        "ok": True,
        "message": "Entire database cleaned back to 0 successfully.",
        "database": db_info,
    })


@app.get("/sessions")
async def list_sessions() -> Response:
    """List previous chat sessions ordered by last updated."""
    k = get_kernel()
    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "memory_sqlite":
            # In turbo cache mode, flush any dirty sessions first so disk has latest state
            if getattr(plugin, "mode", "standard") == "turbo_cache" and getattr(plugin, "cache", None) and getattr(plugin, "db", None):
                plugin.cache.flush_all(plugin.db)
            if getattr(plugin, "db", None):
                sessions = plugin.db.list_sessions(limit=50)
                return jsonify({"ok": True, "sessions": sessions})
    return jsonify({"ok": True, "sessions": []})


@app.get("/sessions/<session_id>/messages")
async def get_session_messages(session_id: str) -> Response:
    """Retrieve chronological messages for a specific session."""
    k = get_kernel()
    limit = 100
    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "memory_sqlite":
            target_db = plugin._get_target_db(session_id)
            msgs = target_db.get_messages(session_id=session_id, limit=limit)
            return jsonify({
                "ok": True,
                "session_id": session_id,
                "messages": msgs,
                "count": len(msgs),
            })
    return jsonify({"ok": False, "error": "memory plugin not found", "messages": []}), 404


@app.post("/sessions/<session_id>/switch")
async def switch_session(session_id: str) -> Response:
    """Notify backend and hydrate session memory cache."""
    from quart import request
    k = get_kernel()
    data = await request.get_json(force=True) if request.is_json else {}
    prev_session_id = data.get("prev_session_id") if isinstance(data, dict) else None

    try:
        await k.bus.publish("session.switch", {
            "prev_session_id": prev_session_id,
            "session_id": session_id,
        })
    except Exception:
        pass

    return jsonify({"ok": True, "session_id": session_id})


@app.delete("/sessions/<session_id>")
async def delete_session(session_id: str) -> Response:
    """Delete a chat session and its associated messages/memories."""
    k = get_kernel()
    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "memory_sqlite":
            if getattr(plugin, "cache", None):
                plugin.cache.evict_session(session_id)
            if getattr(plugin, "db", None):
                plugin.db.delete_session(session_id)
            try:
                await k.bus.publish("session.delete", {"session_id": session_id})
            except Exception:
                pass
            return jsonify({"ok": True, "deleted": session_id})
    return jsonify({"ok": False, "error": "memory plugin not found"}), 404


@app.post("/memory/mode")
async def set_memory_mode() -> Response:
    """Toggle or switch memory mode between 'standard' (A) and 'turbo_cache' (B)."""
    import asyncio
    from quart import request
    k = get_kernel()
    data = await request.get_json(force=True) or {}
    new_mode = str(data.get("mode", "standard")).lower()
    if new_mode not in ("standard", "turbo_cache"):
        return jsonify({
            "ok": False,
            "error": f"Invalid mode '{new_mode}'. Must be 'standard' or 'turbo_cache'."
        }), 400

    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "memory_sqlite":
            if hasattr(plugin, "switch_mode"):
                changed = await plugin.switch_mode(new_mode)
                return jsonify({"ok": True, "mode": new_mode, "changed": changed})
            if getattr(plugin, "mode", "") == new_mode:
                return jsonify({"ok": True, "mode": new_mode, "changed": False})
            plugin.mode = new_mode
            return jsonify({"ok": True, "mode": new_mode, "changed": True})

    return jsonify({"ok": False, "error": "memory_sqlite plugin not found"}), 404


@app.get("/models")
async def models() -> Response:
    """List installed Ollama models."""
    k = get_kernel()
    installed = []
    active = "Unknown"

    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "llm_ollama":
            installed = getattr(plugin, "installed_models", [])
            active = getattr(plugin, "active_model", "Unknown")
            break

    return jsonify({"models": installed, "active": active})


@app.post("/model")
async def set_model() -> Response:
    """Switch the active LLM model."""
    from quart import request
    data = await request.get_json(force=True)
    model_name = data.get("model", "")

    if not model_name:
        return jsonify({"error": "model field required"}), 400

    k = get_kernel()
    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "llm_ollama":
            installed = getattr(plugin, "installed_models", [])
            if model_name not in installed:
                return jsonify({"error": f"model '{model_name}' not installed"}), 404
            plugin._selected_model = model_name
            plugin.active_model = model_name
            logger.info("model switched to %s", model_name)
            return jsonify({"active_model": model_name, "status": "ok"})

    return jsonify({"error": "llm_ollama plugin not loaded"}), 500


@app.post("/vlm_model")
async def set_vlm_model() -> Response:
    """Switch the active Vision / VLM model."""
    from quart import request
    data = await request.get_json(force=True)
    model_name = data.get("model", "")

    if not model_name:
        return jsonify({"error": "model field required"}), 400

    k = get_kernel()
    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "llm_ollama":
            installed = getattr(plugin, "installed_models", [])
            if model_name not in installed:
                return jsonify({"error": f"model '{model_name}' not installed"}), 404
            plugin.vlm_model = model_name
            plugin._selected_vlm_model = model_name
            logger.info("vlm_model switched to %s", model_name)
            return jsonify({"vlm_model": model_name, "status": "ok"})

    return jsonify({"error": "llm_ollama plugin not loaded"}), 500


@app.get("/image_models")
async def get_image_models() -> Response:
    """Return available and active image generation models."""
    k = get_kernel()
    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "tools_builtin":
            return jsonify({
                "active": plugin.get_image_model() if hasattr(plugin, "get_image_model") else "sdxl-lightning",
                "models": plugin.get_available_image_models() if hasattr(plugin, "get_available_image_models") else [],
            })
    return jsonify({"active": "sdxl-lightning", "models": []})


@app.post("/image_model")
async def set_image_model() -> Response:
    """Switch active image generation model."""
    from quart import request
    data = await request.get_json(force=True) or {}
    model_id = str(data.get("model", "")).strip().lower()
    if not model_id:
        return jsonify({"error": "model parameter required"}), 400

    k = get_kernel()
    for plugin in k.plugins:
        if getattr(plugin, "name", "") == "tools_builtin":
            if hasattr(plugin, "set_image_model") and plugin.set_image_model(model_id):
                return jsonify({"ok": True, "active": model_id})
            return jsonify({"error": f"Invalid model '{model_id}'"}), 400

    return jsonify({"error": "tools_builtin plugin not loaded"}), 500


@app.get("/generated/<filename>")
async def get_generated_image_file(filename: str) -> Response:
    """Serve a locally generated image with path verification and CORS support."""
    from quart import send_from_directory
    proj_root = Path(__file__).resolve().parent.parent.parent
    gen_dir = (proj_root / "data" / "generated").resolve()
    file_path = (gen_dir / filename).resolve()

    # Path traversal protection
    try:
        file_path.relative_to(gen_dir)
    except ValueError:
        return jsonify({"error": "Access denied"}), 403

    if not file_path.exists() or not file_path.is_file():
        # Dynamic recovery: render on-demand placeholder PNG so <img> elements never break
        try:
            import io
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (512, 300), color=(18, 20, 26))
            draw = ImageDraw.Draw(img)
            draw.rectangle([(6, 6), (506, 294)], outline=(245, 158, 11), width=2)
            draw.text((256, 100), "Image Missing from Disk", fill=(245, 158, 11), anchor="mm")
            draw.text((256, 140), "File was cleared or archived from data/generated:", fill=(140, 150, 170), anchor="mm")
            draw.text((256, 175), filename, fill=(240, 245, 255), anchor="mm")
            draw.text((256, 230), "Re-generate via chat prompt to create a new visual", fill=(100, 116, 139), anchor="mm")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return Response(
                buf.getvalue(),
                mimetype="image/png",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-cache",
                },
            )
        except Exception:
            return jsonify({"error": "Image not found"}), 404

    resp = await send_from_directory(str(gen_dir), filename)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# ---------------------------------------------------------------------
# Memory Chunks (RAG & AST) API
# ---------------------------------------------------------------------


@app.get("/memory/chunks")
@app.get("/api/memory/chunks")
async def get_memory_chunks() -> Response:
    """Inspect memory chunks loaded from RAG documents and AST code intelligence.

    Query parameters:
      - source: optional document path to load & chunk (PDF, Markdown, TXT)
      - code_path: optional code path to inspect via AST (Python, JS)
      - chunk_size: max chars per chunk (default 600)
      - chunk_overlap: overlap chars (default 100)
    """
    from quart import request
    from core_logic.memory_inspector import inspect_memory

    source = request.args.get("source")
    code_path = request.args.get("code_path")
    try:
        chunk_size = int(request.args.get("chunk_size", 600))
    except (ValueError, TypeError):
        chunk_size = 600

    try:
        chunk_overlap = int(request.args.get("chunk_overlap", 100))
    except (ValueError, TypeError):
        chunk_overlap = 100

    data = await inspect_memory(
        source=source,
        code_path=code_path,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return jsonify(data)


@app.post("/memory/chunks")
@app.post("/api/memory/chunks")
async def post_memory_chunks() -> Response:
    """Load, chunk, or inspect memory chunks via JSON payload.

    Body parameters:
      - source: optional document path or directory
      - code_path: optional code path or directory
      - chunk_size: integer
      - chunk_overlap: integer
    """
    from quart import request
    from core_logic.memory_inspector import inspect_memory

    payload = await request.get_json(force=True) if request.is_json else {}
    if not isinstance(payload, dict):
        payload = {}

    source = payload.get("source")
    code_path = payload.get("code_path")
    try:
        chunk_size = int(payload.get("chunk_size", 600))
    except (ValueError, TypeError):
        chunk_size = 600

    try:
        chunk_overlap = int(payload.get("chunk_overlap", 100))
    except (ValueError, TypeError):
        chunk_overlap = 100

    data = await inspect_memory(
        source=source,
        code_path=code_path,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return jsonify(data)


# ---------------------------------------------------------------------
# WebSocket bridge
# ---------------------------------------------------------------------


@app.websocket("/stream")
async def stream() -> None:
    """Bidirectional EventBus bridge.

    Inbound (UI -> Core): envelopes of shape::

        {"type": "publish", "topic": "...", "payload": {...}}

    Outbound (Core -> UI): envelopes of shape::

        {"type": "event", "topic": "...", "payload": {...}}
        {"type": "pong"}        # keepalive reply
        {"type": "error", "message": "..."}   # protocol errors
    """
    bus = get_kernel().bus
    ws = websocket  # the active Quart websocket

    # The bus subscription uses an async handler that pushes events
    # to the WebSocket. We register it BEFORE sending the ack so
    # the UI can trust the WS is live as soon as it gets the first
    # outbound message.
    outbound_q: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    async def on_event(topic: str, payload: dict[str, Any]) -> None:
        # Fire-and-forget queue put; do NOT block the bus.
        try:
            outbound_q.put_nowait((topic, payload))
        except asyncio.QueueFull:
            logger.warning("stream: outbound queue full, dropping event topic=%r", topic)

    await bus.subscribe("*", on_event)
    logger.info("stream: ws connected, subscribed to bus *")

    # Two concurrent tasks: receive from ws, send to ws.
    send_task = asyncio.create_task(_send_loop(ws, outbound_q))
    try:
        await _recv_loop(ws, bus)
    finally:
        send_task.cancel()
        try:
            await send_task
        except (asyncio.CancelledError, Exception):
            pass
        await bus.unsubscribe("*", on_event)
        logger.info("stream: ws disconnected")


async def _recv_loop(ws, bus: EventBus) -> None:
    """Receive envelopes from the UI and publish them to the bus."""
    while True:
        try:
            raw = await ws.receive()
        except Exception:
            # Connection closed or protocol error. Exit cleanly.
            return
        if raw is None:
            return
        try:
            envelope = parse_envelope(raw)
        except (ValueError, TypeError) as exc:
            await ws.send_json({"type": "error", "message": str(exc)})
            continue
        kind = envelope.get("type")
        if kind == "publish":
            topic = envelope.get("topic", "")
            payload = envelope.get("payload") or {}
            if topic == "user.message":
                from aurora_core.tracer import CYAN, trace_step
                prompt_text = payload.get("text", "")
                trace_step(
                    "1/5",
                    "User Input",
                    "",
                    "Received prompt from UI",
                    f"Prompt: {prompt_text!r} (session: {payload.get('session_id')})",
                    CYAN,
                )
            try:
                await bus.publish(topic, payload)
            except (ValueError, TypeError) as exc:
                await ws.send_json({"type": "error", "message": str(exc)})
        elif kind == "ping":
            await ws.send_json({"type": "pong"})
        else:
            await ws.send_json(
                {"type": "error", "message": f"unknown envelope type: {kind!r}"}
            )


async def _send_loop(ws, q: "asyncio.Queue[tuple[str, dict[str, Any]]]") -> None:
    """Forward bus events to the WS as they arrive."""
    while True:
        topic, payload = await q.get()
        try:
            await ws.send_json(envelope_event(topic, payload))
        except Exception:
            # WS closed mid-send. Drain the queue is pointless;
            # the recv loop will exit and cancel us.
            return


# Lazy import (Quart's websocket types + asyncio live at module top
# in real usage; we keep this below so static type-checkers don't
# choke on the import order during tests).
import asyncio  # noqa: E402  (intentional late import)

# Re-export a tiny TestClientProtocol type so tests can import from
# this module without depending on Quart internals.
__all__ = [
    "app",
    "wire_app",
    "get_kernel",
    "envelope_event",
    "envelope_publish",
    "parse_envelope",
]


# Local re-export so tests don't need to know about the late import
# above. ``TestClientProtocol`` is the type Quart's test client uses
# for ASGI calls; it is the same type the renderer uses over WS.
TestClientProtocol = TestClientProtocol
