"""Live LLM Server Prompting Test Runner.

Tests end-to-end LLM communication through Aurora's local server:
1. Verifies server connectivity (or brings up real in-process Kernel + Quart transport).
2. Prompts LLM via WebSocket (/stream) with user.message.
3. Streams tokens in real-time to terminal.
4. Verifies tool invocation round-tripping through the EventBus.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure Windows stdout handles emojis/UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx
from colorama import Fore, Style, init

init(autoreset=True)

from aurora_core.config import load_config
from aurora_core.kernel import Kernel
from aurora_core.transport import wire_app
from aurora_core.transport.http import app


async def run_prompt_test(client, prompt_text: str, test_title: str) -> bool:
    print(f"\n{Fore.CYAN}============================================================")
    print(f"{Fore.YELLOW}[TEST] {test_title}")
    print(f"{Fore.CYAN}============================================================")
    print(f"{Fore.WHITE}Prompt: {prompt_text!r}\n")

    session_id = f"test_{int(time.time())}"
    tokens = []
    tool_events = []
    completed = False

    async with client.websocket("/stream") as ws:
        # 1. Send user.message over WebSocket
        payload = {
            "type": "publish",
            "topic": "user.message",
            "payload": {
                "text": prompt_text,
                "session_id": session_id,
            },
        }
        await ws.send_json(payload)

        print(f"{Fore.GREEN}--> Sent to server via WebSocket (/stream). Streaming tokens:")
        print(f"{Fore.WHITE}", end="", flush=True)

        # 2. Stream tokens until chat.complete or timeout (90s)
        start_time = time.time()
        while time.time() - start_time < 90.0:
            remaining = max(1.0, 90.0 - (time.time() - start_time))
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            msg_type = msg.get("type")
            topic = msg.get("topic", "")
            data = msg.get("payload", {})

            if msg_type == "event":
                if topic == "llm.token" and data.get("session_id") == session_id:
                    token = data.get("token", "")
                    tokens.append(token)
                    print(token, end="", flush=True)

                elif topic == "tool.execute":
                    tool_name = data.get("tool")
                    tool_args = data.get("args")
                    tool_events.append(f"EXEC: {tool_name}({tool_args})")
                    print(f"\n{Fore.MAGENTA}  [TOOL INVOKED] -> {tool_name}: {tool_args}{Fore.WHITE}\n", end="", flush=True)

                elif topic == "tool.result":
                    tool_events.append(f"RESULT: ok={data.get('ok')}")
                    print(f"{Fore.BLUE}  [TOOL RESULT] -> success={data.get('ok')} | {str(data.get('data'))[:80]}{Fore.WHITE}\n", end="", flush=True)

                elif topic == "chat.complete" and data.get("session_id") == session_id:
                    completed = True
                    break

    print(f"\n\n{Fore.CYAN}------------------------------------------------------------")
    elapsed = round(time.time() - start_time, 2)
    print(f"Status: {'COMPLETED' if completed else 'TIMED OUT'} in {elapsed}s")
    print(f"Total tokens received: {len(tokens)}")
    if tool_events:
        print(f"Tool operations detected: {tool_events}")

    success = completed and len(tokens) > 0
    icon = f"{Fore.GREEN}[PASSED]" if success else f"{Fore.RED}[FAILED]"
    print(f"Result: {icon} {test_title}")
    print(f"{Fore.CYAN}------------------------------------------------------------")
    return success


async def main():
    print(f"{Fore.MAGENTA}============================================================")
    print(f"{Fore.MAGENTA}  AURORA v2 -- LIVE LLM LOCAL SERVER PROMPT TEST RUNNER")
    print(f"{Fore.MAGENTA}============================================================")

    # Check Ollama first
    try:
        async with httpx.AsyncClient(timeout=3.0) as http_client:
            res = await http_client.get("http://localhost:11434/api/tags")
            if res.status_code == 200:
                models = [m.get("name") for m in res.json().get("models", [])]
                print(f"{Fore.GREEN}[OK] Ollama is running with {len(models)} installed models: {models[:3]}...")
            else:
                print(f"{Fore.RED}[FAIL] Ollama HTTP {res.status_code}")
                sys.exit(1)
    except Exception as e:
        print(f"{Fore.RED}[FAIL] Unable to connect to Ollama (http://localhost:11434): {e}")
        sys.exit(1)

    # Bring up the real Kernel and wire the Quart transport app
    print(f"{Fore.YELLOW}[INIT] Initializing Aurora Kernel and wiring Quart Transport...")
    config_path = PROJECT_ROOT / "config" / "aurora.yaml"
    cfg = load_config(config_path)
    kernel = Kernel(cfg)
    await kernel.startup()
    wire_app(kernel)

    # Use Quart test_client to connect to /stream
    test_results = []
    try:
        async with app.test_client() as client:
            # 1. Calculator Tool
            t1 = await run_prompt_test(
                client,
                prompt_text="Calculate sqrt(144) using the calculator tool.",
                test_title="1. Calculator Tool",
            )
            test_results.append(("Calculator", t1))

            # 2. File Search Tool
            t2 = await run_prompt_test(
                client,
                prompt_text="Use the file_search tool to find '*.yaml' files in the 'config' directory.",
                test_title="2. File Search Tool",
            )
            test_results.append(("File Search", t2))

            # 3. Code Execution Tool
            t3 = await run_prompt_test(
                client,
                prompt_text="Use the code_exec tool to run this python code: print('SUM:', sum([10, 20, 30]))",
                test_title="3. Python Code Sandbox (code_exec)",
            )
            test_results.append(("Code Exec", t3))

            # 4. Terminal Tool
            t4 = await run_prompt_test(
                client,
                prompt_text="Use the terminal tool to execute the command: echo AURORA_LIVE_TEST",
                test_title="4. Terminal Command Execution",
            )
            test_results.append(("Terminal (Shell)", t4))

            # 5. Windows OS Automation (get_system_info)
            t5 = await run_prompt_test(
                client,
                prompt_text="Use the get_system_info tool to check my system OS and CPU.",
                test_title="5. Win Automation (get_system_info)",
            )
            test_results.append(("Win Automation (System Info)", t5))

            # 6. Browser Content Fetching (get_page_content)
            t6 = await run_prompt_test(
                client,
                prompt_text="Use the get_page_content tool to extract text from https://example.com",
                test_title="6. Web Page Reader (get_page_content)",
            )
            test_results.append(("Browser (get_page_content)", t6))

            # 7. Web Search (search_web)
            t7 = await run_prompt_test(
                client,
                prompt_text="Use the search_web tool to search for 'Python programming language'.",
                test_title="7. Web Search (search_web)",
            )
            test_results.append(("Browser (search_web)", t7))

    finally:
        print(f"\n{Fore.YELLOW}[CLEANUP] Shutting down Aurora Kernel...")
        await kernel.shutdown()

    # Final summary
    print(f"\n{Fore.CYAN}============================================================")
    print(f"{Fore.CYAN}  FINAL SERVER PROMPT TEST SUMMARY")
    print(f"{Fore.CYAN}============================================================")
    all_ok = True
    for name, passed in test_results:
        status_str = f"{Fore.GREEN}PASSED" if passed else f"{Fore.RED}FAILED"
        print(f"  * {name:<35}: {status_str}")
        if not passed:
            all_ok = False
    print(f"{Fore.CYAN}============================================================")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
