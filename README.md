# Aurora

> **Aurora v2** — A local, privacy-first desktop AI assistant with an async microkernel architecture and extensible plugin ecosystem for Windows.

---

## ⚡ Overview

Aurora is designed to provide high-performance, private, and offline-capable intelligence directly on your personal computer:
- **Local Neural Processing**: Powered by local LLMs (Ollama) and vision perception models (Qwen3-VL).
- **External Image Generation Engine**: Seamlessly bridges to **Fooocus** REST API for zero-VRAM SDXL generation (Juggernaut XL, Speed, Quality, and Anime presets).
- **Deep Document & Code Memory**: Multi-modal Retrieval-Augmented Generation (RAG) over PDFs, Markdown, and source code using Abstract Syntax Tree (AST) analysis.
- **Dual Memory Modes**: Standard disk-backed SQLite or Turbo in-memory session caching.
- **Microkernel Architecture**: Zero concrete cross-plugin coupling; all components communicate strictly via an asynchronous, typed EventBus.

---

## 🏗️ Architecture

```
Electron UI (Vite / React)
         ⇅ (WebSocket & HTTP Envelopes)
Quart Transport Server (127.0.0.1:8765)
         ⇅
Aurora Microkernel Core
   ├─ EventBus (Async Pub/Sub Ring Buffer)
   ├─ Plugin Registry & Lifecycle Manager
   └─ Configuration Engine (YAML + Env Overrides)
         ⇅
Plugins:
   ├─ llm_ollama    (Chat streaming & VLM vision perception)
   ├─ rag_chroma    (Vector store & document indexing)
   ├─ memory_sqlite (Persistent conversations & memory cache)
   └─ tools_builtin (Fooocus image gen, code intel, calculator, terminal)
```

---

## 🚀 Quick Start

### 1. Prerequisites
- **Python 3.11+**
- **Node.js 18+**
- **Ollama** (`ollama pull qwen2.5:3b-instruct-q4_K_M` or similar)
- **Fooocus** (Optional, for SDXL image generation on `http://127.0.0.1:8888`)

### 2. Setup Virtual Environment & Dependencies
```powershell
# Python environment
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Node / Electron environment
npm install
```

### 3. Launching Aurora
```powershell
# Quick launch using the bundled launcher
.\Aurora.bat
```

Or start the backend and frontend separately:
```powershell
# Backend
$env:PYTHONPATH="."
python -m aurora_core

# Frontend
npm run dev
```

---

## 🧪 Testing

Aurora features a comprehensive automated test suite covering event routing, memory inspectors, AST code intelligence, image synthesis, and transport routes:

```powershell
$env:PYTHONPATH="."
pytest tests
```

---

## 📄 License
Personal Project — All rights reserved.
