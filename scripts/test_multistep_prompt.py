"""Multi-Step & Multi-Instruction Prompt Test Runner.

Tests complex multi-step reasoning and multiple tool executions
in a single user conversation through Aurora's local server:
Prompt requests:
1. Calculator evaluation (45 * 12)
2. File search (*.yaml in config/)
3. Combined synthesis in final answer.
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


async def run_multistep_test():
    print(f"{Fore.MAGENTA}============================================================")
    print(f"{Fore.MAGENTA}  AURORA v2 -- MULTI-STEP & MULTI-INSTRUCTION PROMPT TEST")
    print(f"{Fore.MAGENTA}============================================================")

    # Bring up the real Kernel and wire the Quart transport app
    print(f"{Fore.YELLOW}[INIT] Initializing Aurora Kernel and wiring Quart Transport...")
    config_path = PROJECT_ROOT / "config" / "aurora.yaml"
    cfg = load_config(config_path)
    kernel = Kernel(cfg)
    await kernel.startup()
    wire_app(kernel)

    multistep_prompt = (
        "Please complete both of these tasks:\n"
        "1. Use the calculator tool to calculate 45 * 12.\n"
        "2. Use the file_search tool to find the '*.yaml' files in the 'config' directory.\n"
        "Finally, give me a clear summary combining both the calculation answer and the configuration file path found."
    )

    print(f"\n{Fore.CYAN}============================================================")
    print(f"{Fore.YELLOW}[PROMPT SENT]")
    print(f"{Fore.CYAN}============================================================")
    print(multistep_prompt)
    print(f"{Fore.CYAN}============================================================\n")

    session_id = f"multistep_{int(time.time())}"
    tokens = []
    tool_events = []
    completed = False
    full_response = ""

    try:
        async with app.test_client() as client:
            async with client.websocket("/stream") as ws:
                payload = {
                    "type": "publish",
                    "topic": "user.message",
                    "payload": {
                        "text": multistep_prompt,
                        "session_id": session_id,
                    },
                }
                await ws.send_json(payload)

                print(f"{Fore.GREEN}--> Sent to server via WebSocket (/stream). Live output:\n")

                start_time = time.time()
                while time.time() - start_time < 120.0:
                    remaining = max(1.0, 120.0 - (time.time() - start_time))
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
                            full_response += token
                            print(token, end="", flush=True)

                        elif topic == "tool.execute":
                            tool_name = data.get("tool")
                            tool_args = data.get("args")
                            tool_events.append(f"EXEC: {tool_name}({tool_args})")
                            print(f"\n{Fore.MAGENTA}  [TOOL INVOKED] -> {tool_name}: {tool_args}{Fore.WHITE}\n", end="", flush=True)

                        elif topic == "tool.result":
                            tool_events.append(f"RESULT: {data.get('tool')} -> ok={data.get('ok')}")
                            print(f"{Fore.BLUE}  [TOOL RESULT] -> {data.get('tool')}: ok={data.get('ok')} | Data: {str(data.get('data'))[:90]}{Fore.WHITE}\n", end="", flush=True)

                        elif topic == "chat.complete" and data.get("session_id") == session_id:
                            completed = True
                            break

    finally:
        print(f"\n\n{Fore.YELLOW}[CLEANUP] Shutting down Aurora Kernel...")
        await kernel.shutdown()

    elapsed = round(time.time() - start_time, 2)
    print(f"\n{Fore.CYAN}============================================================")
    print(f"{Fore.CYAN}  MULTI-STEP TEST EXECUTION REPORT")
    print(f"{Fore.CYAN}============================================================")
    print(f"Elapsed Time: {elapsed}s")
    print(f"Total Tokens Received: {len(tokens)}")
    print(f"Total Tool Calls: {len([e for e in tool_events if e.startswith('EXEC')])}")
    for event in tool_events:
        print(f"  * {event}")
    print(f"Status: {'COMPLETED' if completed else 'TIMED OUT'}")
    print(f"{Fore.CYAN}============================================================")

    if completed and len(tool_events) >= 2:
        print(f"{Fore.GREEN}--> MULTI-STEP TEST PASSED!")
    else:
        print(f"{Fore.RED}--> MULTI-STEP TEST FAILED!")


if __name__ == "__main__":
    asyncio.run(run_multistep_test())
