"""Transport package.

Holds the HTTP / WebSocket bridge between the kernel and the UI.
See ``http.py`` for the Quart app, ``ipc.py`` for the message
envelope contract, and ``ws.py`` for any future WS-only helpers.
"""

from aurora_core.transport.http import (
    app,
    envelope_event,
    envelope_publish,
    get_kernel,
    wire_app,
)
from aurora_core.transport.ipc import (
    envelope_error,
    envelope_pong,
    parse_envelope,
)

__all__ = [
    "app",
    "wire_app",
    "get_kernel",
    "envelope_event",
    "envelope_publish",
    "envelope_pong",
    "envelope_error",
    "parse_envelope",
]
