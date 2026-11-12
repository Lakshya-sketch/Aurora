"""WebSocket-only helpers (placeholder for Phase 1+).

Phase 1 currently serves WS from the same Quart app as HTTP. This
module is here so future code that needs WS-specific auth, multiple
channels, or per-connection backpressure can land here without
re-shuffling imports.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("aurora.transport.ws")
