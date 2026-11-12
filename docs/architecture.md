# Architecture — Aurora v2

## 1. Top-level shape

```
┌─────────────────────────────────────────────────────────────┐
│  Electron UI  (renderer/)                                  │
│      ↕  WebSocket (streaming) + HTTP (requests)             │
│      ↕  IPC contract (typed envelopes in transport/ipc.py)  │
├─────────────────────────────────────────────────────────────┤
│  Aurora Core (microkernel)                                 │
│   • EventBus       — async pub/sub on topic strings         │
│   • PluginRegistry — contract-based discovery + lifecycle  │
│   • Kernel         — init → register → run → shutdown      │
│   • Config         — YAML, hot-reloadable                  │
├─────────────────────────────────────────────────────────────┤
│  Plugins (all behind BasePlugin contract)                   │
│   LLM · RAG · Memory · Vision (OCR/VLM) · Tools · Voice     │
├─────────────────────────────────────────────────────────────┤
│  Transport (HTTP + WebSocket via Quart)                     │
│  Storage   (SQLite meta, Chroma vectors)                    │
└─────────────────────────────────────────────────────────────┘
```

## 2. Why this shape

Three properties matter more than any individual component choice:

1. **Swappability.** Replace LLM, RAG, OCR, Memory, or Tools by
   dropping a new folder in `plugins/`. Zero changes to core.
2. **Loose coupling.** Plugins never import each other. They publish
   and subscribe on the bus. New features don't ripple.
3. **Cloud-ready.** Each plugin is one folder. Wrapping that folder
   as a microservice (HTTP/gRPC) is mechanical — no core rewrite.

## 3. Plugin contract

```python
class BasePlugin(abc.ABC):
    name: str
    version: str
    capabilities: list[str]   # ["llm.chat", "rag.search", ...]
    requires: list[str]       # ["llm.embed"]
    async def setup(self, ctx: PluginContext) -> None: ...
    async def shutdown(self) -> None: ...
    async def handle_event(self, topic: str, payload: dict) -> None: ...
```

Plugins declare `capabilities` for the agent's tool catalog and
`requires` so the kernel can topological-sort at startup.

## 4. Event topics (canonical catalog)

| Topic | Producer → Consumers | Payload |
|---|---|---|
| `user.message` | UI → Core | `{text, session_id, attachments?}` |
| `llm.prompt.build` | Core → RAG+Memory | `{user_text, session_id}` → augmented |
| `llm.inference` | Core → LLM | `{prompt, model, stream}` |
| `llm.token` | LLM → UI (stream) | `{token, session_id}` |
| `llm.tool_call` | LLM → Tools | `{tool, args, call_id}` |
| `tool.result` | Tools → Core | `{call_id, output}` |
| `rag.index` | UI → RAG | `{file_paths}` |
| `rag.search` | Core → RAG | `{query, k}` → chunks |
| `memory.store` | Core → Memory | `{text, session_id, scope}` |
| `memory.retrieve` | Core → Memory | `{query, session_id, k}` |
| `vision.capture` | Core → Vision | `{method}` |
| `vision.ocr` | Vision → Core | `{text}` |
| `vision.vlm` | Vision → Core | `{description}` |

Add a new topic? Add it here AND in `aurora_core/event_bus.py`'s topic
catalog docstring.

## 5. Data flow for a single chat turn (happy path)

```
1. UI  → user.message   (text)
2. Core publishes llm.prompt.build
   - Memory plugin retrieves recent + relevant memories
   - RAG plugin retrieves top-k chunks
   - Returns augmented prompt
3. Core publishes llm.inference  (with augmented prompt)
4. LLM plugin streams llm.token events → UI renders
5. If LLM emits tool_call → Core publishes tool.result
6. Loop back to step 3 with tool outputs in context
7. Final token → Core publishes chat.complete to UI
```

## 6. Storage

- **`data/aurora.db`** — SQLite. Tables: `sessions`, `messages`,
  `memories`, `tool_invocations`, `plugin_state`.
- **`data/chroma/`** — ChromaDB persistence directory.
- **`data/inbox/`** — drag-drop target for RAG indexing.
- **`data/aurora.log`** — rotating file log via Python `logging`.

## 7. Backend framework

Quart over Django: async-native, ASGI-ready, lightweight, plays well
with WebSockets for streaming tokens without bolting on Channels.

## 8. LangGraph placement

LangGraph lives in `core_logic/orchestrator.py`, NOT inside the LLM
plugin. The orchestrator depends only on `BasePlugin` capabilities.
This way, swapping LLM implementations doesn't drag LangGraph
rewrites with it.

## 9. Future microservices path

When/if we move to cloud, each plugin becomes a service:

```
              ┌────────────────┐
              │  Aurora Core   │  (now an orchestrator only)
              └───────┬────────┘
                      │ (HTTP / gRPC)
   ┌──────────┬───────┼────────┬──────────┐
   ▼          ▼       ▼        ▼          ▼
 rag-svc  memory-svc llm-svc tools-svc vision-svc
```

The `EventBus` interface stays the same; the implementation swaps to
a network pub/sub (NATS/Redis). Plugin `setup/shutdown` become
service connect/disconnect. The Python core shrinks to ~200 LOC.

## 10. Anti-patterns explicitly rejected

- **Direct plugin-to-plugin imports** (creates coupling)
- **LangGraph inside the LLM plugin** (locks agent to one LLM impl)
- **Django monolith** (slower iteration, framework-baked opinions)
- **Subprocess-as-plugin** (no lifecycle, no crash recovery)
- **Background daemons we can't kill** (operational hazard)

## 11. Open architectural questions (for user input)

- Hot-reloading individual plugins at runtime vs config-only reload
- Per-user vs per-machine config precedence
- Multi-session concurrency: one agent run per session, or pool?
  Default: one agent run per session (simpler, sufficient).
