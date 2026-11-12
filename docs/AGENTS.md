# AGENTS.md — Aurora v2

Root instruction file for AI coding agents working on this project.
**Read this before doing anything in the repo.** It overrides defaults.

---

## What this project is

Aurora v2 is a **local desktop LLM assistant** for Windows with native OS
integration (file system, windows, input automation, screen awareness).
It is a deliberate rewrite of v1 (`E:/NeuralChat/`) using a
**microkernel + event-driven** architecture where every capability
(RAG, Memory, OCR/VLM, Tools, LLM Runtime) is a separate, swappable
plugin behind one contract (`BasePlugin`). Future goal: same plugins
can be lifted into microservices without rewriting the core.

See `prd.md` for product scope and `architecture.md` for system design.

---

## Tech stack (locked — do NOT change without an explicit user request)

- **Backend:** Python 3.11+, Quart (async ASGI), Uvicorn
- **Agent:** LangGraph (inside `core_logic/orchestrator.py`)
- **Event bus:** in-process asyncio pub/sub (`aurora_core/event_bus.py`)
- **Vector store:** ChromaDB (default; swappable via `Retriever` interface)
- **Embeddings:** sentence-transformers (`all-MiniLM-L6-v2` default)
- **LLM runtime:** Ollama (local HTTP API at `http://localhost:11434`)
- **Memory:** SQLite (stdlib) + embeddings on stored turns
- **OCR/VLM:** EasyOCR + multimodal Ollama model (e.g. `llava`)
- **Win automation:** PyAutoGUI + ctypes; upgrade to UIAutomation later
- **UI:** Electron 28+, vanilla JS, native Windows file dialogs via
  Electron `dialog` API (not HTML `<input type=file>`)
- **IPC:** Electron `webContents.send` + WebSocket for streamed tokens
- **Config:** YAML (`config/aurora.yaml`), hot-reloadable
- **Plugin discovery:** filesystem folders under `plugins/*/plugin.py`
- **Tests:** pytest, pytest-asyncio
- **Formatting:** black, isort; line-length 100

---

## Repo layout (canonical)

```
E:/NeuralChat2/
├── AGENTS.md                ← you are here
├── prd.md
├── architecture.md
├── implementation_plan.md
├── .gitignore
├── .env.example
├── README.md
├── Aurora.bat
├── requirements.txt
├── package.json
├── config/aurora.yaml
├── electron/{main,preload}.js
├── renderer/{index.html,app.js,styles.css}
├── aurora_core/             microkernel (kernel, event_bus, registry,
│                            plugin_base, config, transport/)
├── core_logic/              orchestrator, query_router, prompt_builder,
│                            context_builder
├── plugins/                 llm_ollama, rag_chroma, memory_sqlite,
│                            vision_easyocr, tools_builtin, voice_edge
└── tests/
```

Do NOT consolidate files the architecture separates.
Do NOT add new top-level folders without an entry in `architecture.md`.

---

## Build & run commands

```bash
# Install (Python)
python -m venv venv
./venv/Scripts/activate          # Windows
pip install -r requirements.txt

# Install (Node/Electron)
npm install

# LLM (one-time) — install Ollama + a base model
ollama pull llama3.1:8b
ollama pull llava                # for VLM

# Run backend
./venv/Scripts/python -m aurora_core

# Run Electron UI
npm start

# One-shot launcher (Windows)
./Aurora.bat
```

## Test commands

```bash
pytest -q                           # all
pytest tests/test_event_bus.py -v  # one file
pytest -k "swap" -v                 # plugin-swap proof test
```

---

## Rules every agent MUST follow

1. **Never hardcode paths.** Use `config/aurora.yaml` values + helpers
   in `aurora_core/config.py`. The only allowed literal is the project
   root: `E:/NeuralChat2/`.
2. **Never import across plugins.** Plugins talk only via the
   `EventBus`. The agent (`core_logic/orchestrator.py`) reads events.
3. **Never import a concrete implementation in `core_logic`.** Depend on
   `BasePlugin` capabilities only — this is what makes swappability real.
4. **Native UX first.** File pickers = Electron `dialog.showOpenDialog`,
   not HTML inputs. Notifications = Electron `Notification`, not
   `alert()`. Screenshots = Electron `desktopCapturer`.
5. **FIX: comments are documentation.** When you fix a bug, leave a
   `# FIX:` comment explaining the original symptom + cause. When you
   *find* a bug, leave a `# BUG:` marker for the user.
6. **No background services we can't restart.** Anything you spawn,
   you must be able to kill cleanly. No `subprocess.Popen` without a
   tracking handle.
7. **Windows is the target.** Tested on Windows 10/11. Don't add a
   Linux/macOS codepath unless it's identical behavior.
8. **Commit messages:** `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`,
   `test:`. Imperative mood. Lowercase. Reference the file/module.
9. **No silent failures.** Logs at INFO for normal flow, WARNING for
   recoverable, ERROR + stack for unrecoverable. Always log to
   `data/aurora.log` via the kernel logger.
10. **Phases are atomic.** Do not start Phase N+1 until Phase N's
    verification step in `implementation_plan.md` is green.
11. **Progress Journaling.** After responding to or completing each user prompt,
    the agent MUST write a new detailed entry at the top of `progress.md` logging
    what was asked, what was changed, files created/modified, and verification status.
12. **Context Retrieval from Progress.** At the start of handling a prompt/task,
    the agent MUST read the last 2 entries from `progress.md` to restore full
    active context before proceeding.

---

## Plugin contract (the single most important thing in this repo)

```python
# aurora_core/plugin_base.py
class BasePlugin(abc.ABC):
    name: str
    version: str
    capabilities: list[str]   # ["llm.chat", "rag.search", ...]
    requires: list[str]       # ["llm.embed"]

    async def setup(self, ctx: PluginContext) -> None: ...
    async def shutdown(self) -> None: ...
    async def handle_event(self, topic: str, payload: dict) -> None: ...
```

Subclass this. Declare capabilities. Subscribe to topics. Done.

---

## When unsure

- Read `architecture.md`.
- If still unsure, ask the user before coding. Do NOT guess and ship.
- If you must guess, mark it `# TODO:` and surface it in your final message.
