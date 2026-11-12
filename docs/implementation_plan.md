# Implementation Plan — Aurora v2

Phased, TDD-friendly, atomic. Each phase has a verification gate.
Do not start phase N+1 until phase N's gate is green.

## Phase 0 — Skeleton + contract  (`aurora_core` only)

**Outcome:** Hello-world plugin loads, subscribes, emits, receives.

- [ ] Folder layout (see `AGENTS.md`)
- [ ] `aurora_core/event_bus.py` — async pub/sub, topic filters, history
- [ ] `aurora_core/plugin_base.py` — `BasePlugin` + `PluginContext`
- [ ] `aurora_core/registry.py` — discover `plugins/*/plugin.py`, load
- [ ] `aurora_core/config.py` — YAML load + env overlay
- [ ] `aurora_core/kernel.py` — startup sequence: config → plugins → transport
- [ ] `tests/test_event_bus.py`, `tests/test_plugin_lifecycle.py`
- [ ] **Verify:** running `python -m aurora_core` finds 1 test plugin, fires test event, prints receipt.

## Phase 1 — Transport

**Outcome:** Electron sends a chat message, gets streamed reply.

- [x] `aurora_core/transport/http.py` — Quart routes: `/health`, `/plugins`, `/config`
- [x] `aurora_core/transport/ws.py` — WebSocket `/stream`, typed envelopes
- [x] `aurora_core/transport/ipc.py` — message envelope shared with `preload.js`
- [x] `electron/main.js`, `electron/preload.js`
- [x] `renderer/index.html`, `renderer/app.js` (chat shell), `renderer/styles.css`
- [x] `package.json`, `npm install`
- [x] `plugins/echo_stub/plugin.py` — dummy streamer for Phase 1 gate
- [x] `Aurora.bat` — one-shot launcher
- [x] **Verify:** smoke test green (2 plugins), 30 pytest tests pass, Electron shell + renderer built.

## Phase 2 — LLM plugin + orchestrator

**Outcome:** Real chat with local Ollama, streamed tokens.

- [x] `plugins/llm_ollama/plugin.py` — wraps Ollama `/api/chat`, streams on `llm.token`
- [x] `core_logic/orchestrator.py` — agent orchestrator, subscribes to `user.message`
- [x] `core_logic/prompt_builder.py` — identity prompt + user text assembly
- [x] `requirements.txt` — `httpx` verified & ready
- [x] **Verify:** 36 pytest tests pass, smoke test green (`llm_ollama` loaded), streaming and error fallback handling verified.

## Phase 3.0 — Core Builtin Tools & Execution Sandbox

**Outcome:** Tools plugin skeleton, Tool Dispatcher, Math Calculator, File Search, Code Execution, and Safe Terminal execution.

- [x] `plugins/tools_builtin/plugin.py` — Plugin lifecycle & Tool Dispatcher over `EventBus` (`tool.execute`)
- [x] `plugins/tools_builtin/handlers/calculator.py` — Safe math evaluation (`2^10`, formulas)
- [x] `plugins/tools_builtin/handlers/file_search.py` — Disk file search & text content reader
- [x] `plugins/tools_builtin/handlers/code_exec.py` — Python / JS code execution sandbox
- [x] `plugins/tools_builtin/handlers/terminal.py` — Safe terminal command execution (PowerShell/CMD)
- [x] `tests/test_tools_builtin.py` — Unit tests for Phase 3.0 handlers
- [x] **Verify:** 68 pytest tests pass (36 previous + 32 new). "what's 2^10?" → calculator returns 1024.

## Phase 3.1 — Native Desktop Automation & Web Scraping

**Outcome:** Windows native app control, PyAutoGUI/Win32 automation, web browsing, and LLM Tool Calling loop.

- [x] `plugins/tools_builtin/handlers/win_automation.py` — ctypes/Win32 (`open_app`, `get_window_list`, `focus_window`, `get_system_info`)
- [x] `plugins/tools_builtin/handlers/browser.py` — Web content fetcher (`open_url`, `get_page_content`, `search_web`)
- [x] Integration with `plugins/llm_ollama/plugin.py` for LLM Function Calling tool-use loop (tool schemas → Ollama API → tool_calls → execute → re-invoke)
- [x] **Verify:** 68 pytest tests pass. Vite build green. Tool-calling loop wired end-to-end.

## Phase 4.1 — AST / Symbol Graph (Code Intelligence)

**Outcome:** Deterministic code structure parsing (classes, functions, imports, callers) via AST / RepoMap with zero LLM hallucination.

- [ ] `plugins/tools_builtin/handlers/code_intel.py` — AST parser for Python/JS/TS code; extracts symbols, functions, classes, imports, and call hierarchies
- [ ] Expose `code_intel` tools to LLM function calling loop (`get_symbol_map`, `find_definitions`, `find_callers`)
- [ ] `tests/test_code_intel.py` — Unit tests for AST parsing and symbol extraction
- [ ] **Verify:** "Show me all functions in `core_logic/orchestrator.py`" returns exact AST without hallucination.

## Phase 4.2 — Vector RAG Plugin (Document Ingestion & Semantic Search)

**Outcome:** Drop a PDF/doc; "summarize it" returns cited answer.

- [ ] `plugins/rag_chroma/plugin.py` — `Retriever` interface subscribing to `rag.index` and `rag.search`
- [ ] `plugins/rag_chroma/document_loader.py` — Multi-format text extraction (PDF via `PyPDF2`, TXT, MD) + recursive chunking
- [ ] `plugins/rag_chroma/embeddings.py` — Local vector embeddings via Ollama `/api/embed`
- [ ] `plugins/rag_chroma/store.py` — Vector index, cosine similarity search, top-k retriever
- [ ] `core_logic/context_builder.py` — Injects retrieved document excerpts into LLM system context
- [ ] `data/inbox/` drag-drop wired through Electron & `InputBar.jsx`
- [ ] `tests/test_rag.py` — Unit & integration tests
- [ ] **Verify:** index a PDF, chat cites it.

## Phase 5 — Memory plugin

**Outcome:** "What did I ask yesterday" recalls.

- [ ] `plugins/memory_sqlite/plugin.py` — conversation + semantic memory
- [ ] Auto-recall in `prompt_builder`
- [ ] **Verify:** seed memory, ask cross-session question, get it back.

## Phase 6 — Vision (OCR/VLM)

**Outcome:** "What's on my screen?" returns structured description.

- [ ] `plugins/vision_easyocr/plugin.py` — OCR + VLM
- [ ] VLM uses same LLM plugin (multimodal Ollama)
- [ ] **Verify:** screenshot → OCR text + llava description.

## Phase 7 — Voice (v2.1)

- [ ] `plugins/voice_edge/plugin.py` — Edge-TTS + Whisper STT
- [ ] Mic button in UI

## Phase 8 — Polish

- [ ] Hot-reload `aurora.yaml` (kernel subscribes to config FS events)
- [ ] Graceful single-plugin shutdown
- [ ] `/health` lists capabilities + last activity
- [ ] `README.md`, first-run wizard

---

## Test strategy

- **Unit:** core/event_bus, core/registry, plugin_base contract.
- **Integration:** kernel + transport + stub plugins.
- **Swap proof:** `tests/test_rag_swap.py` — load Chroma, swap FAISS stub at runtime, prove no core change required.
- **Manual smoke:** per phase (see verify step in each phase).

## Commit cadence

One commit per task box. Commit messages: `feat:`, `fix:`, `chore:`,
`docs:`, `refactor:`, `test:`. Reference the touched module.

## Risk register

- Quart + LangGraph mixing sync/async → run orchestrator in threadpool.
- Chroma single-process is fine for desktop; document the boundary.
- PyAutoGUI focus issues on Win → add `pywinauto` if needed in Phase 3.
- Hot plugin reload is genuinely hard; v2 only reloads config, not code.

## What this plan does NOT cover

- Cloud deployment (interface only; build it when we need it)
- Production hardening (auth, rate limits, secrets management beyond `.env`)
- Plugin marketplace / signed plugins
