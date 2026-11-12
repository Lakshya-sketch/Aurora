# Aurora v2

> Local, privacy-first desktop LLM assistant for Windows. Microkernel
> architecture: every capability is a swappable plugin.

**Status:** pre-implementation. See `prd.md`, `architecture.md`, and
`implementation_plan.md` for what's being built and why.

## Quick start (Windows)

```bat
:: 1. Install Ollama + a model
ollama install
ollama pull llama3.1:8b
ollama pull llava

:: 2. Install Python deps
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

:: 3. Install Node deps
npm install

:: 4. Copy env template
copy .env.example .env

:: 5. Launch
Aurora.bat
```

## What you get (when v2 is finished)

- Chat with streamed tokens from a local LLM
- RAG over your PDFs / code / notes
- Long-term memory across sessions
- Tool use (calculator, browser, GitHub, terminal, file ops)
- Windows automation (open apps, type, click, capture)
- Screenshot → OCR → VLM "what's on my screen"
- Native Windows file dialogs

## Architecture in one screen

```
Electron UI ⇄ Transport ⇄ Aurora Core (microkernel) ⇄ Plugins
                                       ⇡              ⇡
                                       EventBus (asyncio)
```

Every plugin (LLM, RAG, Memory, Vision, Tools) sits behind one
contract: `aurora_core/plugin_base.BasePlugin`. Replace one
implementation, nothing else changes.

Full design: `architecture.md`. Phased plan: `implementation_plan.md`.

## For AI agents

Read `AGENTS.md` before touching this repo. It defines the tech stack,
folder layout, build commands, and rules of engagement. If anything
in this README conflicts with `AGENTS.md`, **AGENTS.md wins** for
agents. Humans may read both.

## License

Personal project. License TBD.
