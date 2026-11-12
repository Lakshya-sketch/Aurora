# PRD — Aurora v2 (Local Desktop LLM Assistant)

## 1. Vision

A local, privacy-first desktop LLM assistant for Windows that can see
the screen, manipulate files and apps, remember past work, and answer
from your own documents — without sending data anywhere.

v2 is a clean-slate rewrite of Aurora v1. The product thesis is
unchanged; the architecture is rebuilt so that every capability
(RAG, memory, OCR, tools, LLM) is a swappable plugin behind one
contract, making the system extensible and future-cloud-ready.

## 2. Target user

A single power user (Windows, technical, comfortable with local
LLMs) who wants an always-available assistant that can:

- Chat with their **local files** (RAG over PDFs, code, notes)
- **Remember** past conversations and facts about them
- **See and act on the screen** via screenshots and Win automation
- Use **tools** (calculator, browser, terminal, GitHub, file ops)
- Run **fully offline** — no telemetry, no cloud calls

## 3. Core features (v2 must-have)

| ID | Feature | Notes |
|---|---|---|
| F1 | Local chat with streamed tokens | Ollama, default model `llama3.1:8b` |
| F2 | RAG over user-uploaded docs | Drag-drop a folder, index, query |
| F3 | Long-term memory | Conversation + facts, persisted |
| F4 | Tool use via LLM | Calculator, browser, FS, terminal, GitHub |
| F5 | Windows automation | Type, click, capture window list, open apps |
| F6 | Screenshot + OCR + VLM | "What's on my screen?" |
| F7 | Native Win file dialogs | Electron `dialog`, not HTML inputs |
| F8 | Plugin system | Add/replace a plugin with folder drop |
| F9 | Hot-reloadable config | `config/aurora.yaml` |
| F10 | Health/status UI | Show loaded plugins + capabilities |

## 4. Out of scope for v2 (explicit non-goals)

- Cloud deployment (the architecture allows it; we don't build it)
- Multi-user / accounts / auth
- Mobile companion
- Plugin marketplace, signed plugins, third-party plugin hosting
- Distributed event bus (Redis/NATS) — interface is ready, impl is local
- Voice (STT/TTS) — deferred to v2.1
- Voice wake-word — out
- Cloud LLM providers (no OpenAI/Anthropic keys) — local only
- Telemetry, analytics, error reporting
- Cross-platform (macOS/Linux) — Windows 10/11 only

## 5. Success criteria

A user can:

1. Install, run `Aurora.bat`, see the UI.
2. Drag a folder of PDFs onto the chat; ask "summarize Q3.pdf" and
   receive a cited answer.
3. Ask "what was the path of the bug we discussed yesterday" and get
   the right answer from memory.
4. Ask "summarize the active window" — Aurora screenshots, OCRs,
   uses VLM, returns a structured summary.
5. Ask "open Notepad and type 'hello world'" — Aurora does it.
6. Stop the app, swap the RAG plugin from Chroma to FAISS by editing
   `aurora.yaml` and restarting — nothing else in the system changes.

## 6. UX principles

- **Native first.** File dialogs, notifications, clipboard are OS-native.
- **Streaming by default.** Tokens render as they arrive, no "loading…".
- **Failure is visible.** Don't swallow errors. Show them in the UI.
- **One-shot answers if possible.** Tool chains shouldn't make the user
  click "continue" repeatedly.

## 7. Non-functional requirements

- **Latency:** first-token < 800ms on local Ollama `llama3.1:8b`, 16GB RAM
- **Memory:** < 1.5GB resident with no RAG index loaded
- **Disk:** project < 200MB excluding models and user data
- **Crash safety:** index files recoverable; tool execution sandboxed

## 8. References

- v1 codebase: `E:/NeuralChat/` (for context, not source-import)
- Architecture rationale: `architecture.md`
- Phase-by-phase plan: `implementation_plan.md`
- Agent rules of engagement: `AGENTS.md`
