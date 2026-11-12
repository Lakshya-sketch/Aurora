"""Live Route & Event Sequence Tracer for Aurora v2.

Prints clean, human-readable terminal lines showing how prompts move
step-by-step through components and tools.
"""

from __future__ import annotations

import sys
from typing import Any

# ANSI Color codes for high visibility in Windows terminal
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def trace_step(
    seq_num: int | str,
    component: str,
    path: str,
    action: str,
    detail: str = "",
    color: str = CYAN,
) -> None:
    """Print a clean, easy-to-read 2-line step summary to stderr."""
    line1 = f"\n{color}{BOLD}➡️  [{seq_num}] {component.upper()}{RESET} ➔ {action}"
    sys.stderr.write(line1 + "\n")
    if detail:
        sys.stderr.write(f"    └─ {detail}\n")
    sys.stderr.flush()
