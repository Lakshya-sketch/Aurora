# 🧰 Tools Specification — Aurora v2

> **For:** `core_logic/orchestrator.py` (LangGraph) + `plugins/tools_builtin/`
> **Source of truth:** this file. Implementation must match names + descriptions.
> **Cross-check:** every tool here must have a handler file at
> `plugins/tools_builtin/handlers/<name>.py` exposing a `ToolSpec`.

This document defines every tool Aurora v2 can invoke. Tools are
deterministic, single-purpose, and (where dangerous) gated behind
confirmation. They are grouped by the **plugin that owns them** — not by
domain — because in the microkernel, each plugin owns the surface it
serves. Implementation mirrors the v1 catalog (`E:/NeuralChat/tools.md`)
where applicable; v2 changes are marked with **(v2)**.

---

## 🧠 Design Principles

* One tool = one task. No multi-purpose tools.
* Inputs are validated **at the handler boundary**, not deep inside.
* Outputs are predictable: typed envelopes, never raw `print`.
* Dangerous operations (filesystem delete, shell exec, automation that
  moves the cursor) require confirmation via the `confirm_action` tool.
* Tools are **stateless**. State lives in plugins (e.g., RAG index in
  Chroma, memory in SQLite).
* A tool is exposed to the LLM only if its handler's spec is registered
  with `plugins/tools_builtin/plugin.py` at startup.

---

## Tool envelope (shared contract)

Every handler returns this shape:

```json
{
  "ok": true,
  "data": <tool-specific>,
  "error": null
}
```

On failure:

```json
{
  "ok": false,
  "data": null,
  "error": "human-readable reason"
}
```

---

## Plugin: `plugins/tools_builtin`  (built-in tools)

This plugin owns the surface that interacts with the host OS, the
filesystem, the shell, the browser, and Windows UI automation.
One Python file per tool. Each exports a `ToolSpec`:

```python
ToolSpec(
    name="create_folder",
    description="Create a new folder at given path",
    args=ArgsSchema({
        "path": Arg(str, required=True, description="Absolute or project-relative path"),
    }),
    handler=create_folder_impl,
    dangerous=False,
)
```

---

### 1. File System  (was v1 §1)

| Tool (v2 name) | Description | Args | Dangerous |
|---|---|---|---|
| `create_folder`  | Create a new folder at given path. | `path: str` | no |
| `create_file`    | Create a file with optional content. | `path: str, content: str = ""` | no |
| `read_file`      | Read file content. | `path: str` | no |
| `delete_file`    | Delete a file. | `path: str` | **yes** (confirm) |
| `list_files`     | List files in directory. | `directory: str, recursive: bool = False` | no |
| `move_file`      | Move file from source to destination. | `src: str, dest: str` | **yes** (confirm) |
| `rename_file`    | Rename a file. | `old_name: str, new_name: str` | **yes** (confirm) |

> **v2 change:** `delete_file` uses `send2trash` by default (v1 used
> `os.remove`). Hard-delete is opt-in via `permanent: bool = True`.

---

### 2. System Control  (was v1 §2)

| Tool | Description | Args | Dangerous |
|---|---|---|---|
| `open_app`        | Open an application by name or exe path. | `app_name: str` | no |
| `close_app`       | Close an application by process name. | `app_name: str` | **yes** (confirm) |
| `run_command`     | Execute a shell command via `subprocess.run`. | `command: str, cwd: str = None, timeout_s: int = 30` | **yes** (confirm) |
| `get_system_info` | Get CPU, RAM, OS, disk info. | none | no |

> **v2 change:** `run_command` has a default 30s timeout. Long-running
> commands should be redesigned as a workflow, not a single tool call.

---

### 3. Screen Awareness  (was v1 §3) — **delegates to `vision_easyocr` plugin**

These tools live in `plugins/tools_builtin/handlers/` but **call into
the `vision_easyocr` plugin via the EventBus**. They are thin shims —
the real OCR/VLM work happens in the vision plugin.

| Tool | Description | Args | Dangerous |
|---|---|---|---|
| `take_screenshot`   | Capture current screen, save to `data/.cache/`. Returns path. | none | no |
| `read_screen_text`  | Extract text from screen using OCR. | none | no |
| `get_active_window` | Get current active window title. | none | no |

> **v2 change:** tool remains, but the screen-capture primitive is
> owned by `vision_easyocr`. The handler publishes a `vision.capture`
> event and awaits the result.

---

### 4. Input Automation  (was v1 §4)  →  `tools_builtin/handlers/win_automation.py`

Windows-specific, implemented on top of **PyAutoGUI** today,
**UIAutomation API** as a future alternative (interface `WinInput`).

| Tool | Description | Args | Dangerous |
|---|---|---|---|
| `type_text`  | Type given text into the focused control. | `text: str, interval: float = 0.0` | **yes** (confirm) |
| `press_key`  | Press a keyboard key (or combo like `ctrl+c`). | `key: str` | **yes** (confirm) |
| `click`      | Click at coordinates. | `x: int, y: int, button: str = "left"` | **yes** (confirm) |
| `scroll`     | Scroll screen at current mouse position. | `amount: int, direction: str = "down"` | no |
| `get_window_list` **(v2 new)** | Enumerate top-level windows with titles + HWNDs. | none | no |
| `focus_window` **(v2 new)** | Focus a window by title (substring match). | `title_contains: str` | yes (confirm) |

> **v2 change:** added `get_window_list` + `focus_window` so the agent
> can act contextually instead of coordinate-blind.

---

### 5. Browser  (was v1 §5)

| Tool | Description | Args | Dangerous |
|---|---|---|---|
| `open_url`         | Open a URL in default browser. | `url: str` | no |
| `search_web`       | Perform web search via DuckDuckGo (no API key). | `query: str, max_results: int = 5` | no |
| `get_page_content` | Fetch + extract readable text from a webpage. | `url: str` | no |

> **v2 change:** no Google API key required. Uses `duckduckgo-search`
> + `trafilatura` for clean extraction.

---

### 6. RAG / Knowledge  (was v1 §6) — **delegates to `rag_chroma` + `memory_sqlite`**

In v2 these are **first-class plugin capabilities**, not tools. The
LLM dispatches via EventBus topics, not via the tool interface.
They are listed here so the capability surface is complete:

| Capability | Topic | Description |
|---|---|---|
| `rag.index_documents`   | `rag.index`   | Index documents into vector DB. |
| `rag.search_documents`  | `rag.search`  | Search indexed documents. |
| `memory.store`          | `memory.store` | Store conversation memory. |
| `memory.retrieve`       | `memory.retrieve` | Retrieve relevant memory. |

> **v2 change:** NOT tools. The orchestrator calls them as plugin
> capabilities before invoking the LLM. (Keeping them in a table here
> for parity with v1 catalog.)

---

### 7. Workflow  (was v1 §7) — **(v2 deferred)**

`run_workflow` is **deferred to v2.1**. The microkernel architecture
makes workflows a future "Workflow plugin" instead of a hardcoded
tool. Expected shape:

```
plugins/workflow_basic/plugin.py
  + handlers/<workflow_name>.py
```

Tracked in `implementation_plan.md` Phase 8 (Polish) backlog.

---

### 8. Voice  (was v1 §8) — **(v2 deferred to v2.1)**

| Tool | Status | Owner plugin (planned) |
|---|---|---|
| `speech_to_text` | deferred to v2.1 | `plugins/voice_edge` |
| `text_to_speech` | deferred to v2.1 | `plugins/voice_edge` |

---

### 9. Safety  (was v1 §9) — **promoted to plugin-wide concern**

The safety tools are not exposed as LLM-facing tools. They are
**internal contracts** used by handler code:

| Internal | Description | Called by |
|---|---|---|
| `confirm_action`    | Ask user for confirmation before dangerous ops. | every `dangerous=True` tool |
| `validate_path`     | Reject paths outside `AURORA_HOME` / sandbox. | every FS tool |
| `restrict_access`   | Blocklist for system dirs (Windows, Program Files). | every FS + shell tool |

> **v2 change:** the LLM never calls `confirm_action` itself. The
> kernel prompts the user through the Electron UI when a handler
> raises `ConfirmationRequired`.

---

## Tool → Handler file map  (canonical)

```
plugins/tools_builtin/handlers/
├── __init__.py
├── create_folder.py
├── create_file.py
├── read_file.py
├── delete_file.py
├── list_files.py
├── move_file.py
├── rename_file.py
├── open_app.py
├── close_app.py
├── run_command.py
├── get_system_info.py
├── take_screenshot.py     (shim → vision_easyocr)
├── read_screen_text.py    (shim → vision_easyocr)
├── get_active_window.py
├── type_text.py
├── press_key.py
├── click.py
├── scroll.py
├── get_window_list.py     (v2 new)
├── focus_window.py        (v2 new)
├── open_url.py
├── search_web.py
├── get_page_content.py
└── win_automation.py      (helper, not a tool)
```

---

## v1 → v2 tool changes (summary)

| v1 name | v2 status |
|---|---|
| create_folder           | kept (handler rewritten) |
| create_file             | kept |
| read_file               | kept |
| delete_file             | kept, default = send2trash |
| list_files              | kept |
| move_file               | kept |
| rename_file             | kept |
| open_app                | kept |
| close_app               | kept |
| run_command             | kept, default timeout 30s |
| get_system_info         | kept |
| take_screenshot         | kept (now shim → vision plugin) |
| read_screen_text        | kept (now shim → vision plugin) |
| get_active_window       | kept |
| type_text               | kept |
| press_key               | kept |
| click                   | kept |
| scroll                  | kept |
| open_url                | kept |
| search_web              | kept (engine: DuckDuckGo) |
| get_page_content        | kept |
| index_documents         | promoted to plugin capability (not a tool) |
| search_documents        | promoted to plugin capability (not a tool) |
| store_memory            | promoted to plugin capability (not a tool) |
| retrieve_memory         | promoted to plugin capability (not a tool) |
| run_workflow            | deferred to v2.1 |
| speech_to_text          | deferred to v2.1 |
| text_to_speech          | deferred to v2.1 |
| confirm_action          | demoted to internal contract |
| validate_path           | demoted to internal contract |
| restrict_access         | demoted to internal contract |
| — **(v2 new)**          | `get_window_list` |
| — **(v2 new)**          | `focus_window` |

---

## Tool registry loading order

1. Kernel starts up, loads `plugins/tools_builtin`.
2. Plugin calls `plugin.py:register_tools(...)` to import every handler
   in `handlers/` and build a `dict[str, ToolSpec]`.
3. Registry is published to the LLM orchestrator on each turn (filtered
   by `enabled` flag from `config/aurora.yaml` → `plugins.tools_builtin.enabled_tools`).
4. LLM emits a tool call → kernel dispatches to handler → handler's
   `dangerous=True` tools trigger UI confirmation first.

---

## Cross-references

- v1 source catalog: `E:/NeuralChat/tools.md`
- v1 capture in this repo: `.hermes/plans/_tools_v1_capture.md`
- Plugin contract + agent rules: `AGENTS.md`
- Architecture + plugin contract: `architecture.md` §3
- Event topics: `architecture.md` §4
- Phase 3 implementation plan: `implementation_plan.md`
