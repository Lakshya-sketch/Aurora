"""IPC envelope schema shared between the kernel and the renderer.

Every WebSocket message is a JSON object with a ``type`` field. The
two message types Phase 1 ships:

  Inbound (renderer -> kernel):
    {"type": "publish", "topic": "...", "payload": {...}}
    {"type": "ping"}

  Outbound (kernel -> renderer):
    {"type": "event", "topic": "...", "payload": {...}}
    {"type": "pong"}
    {"type": "error", "message": "..."}

This is the contract that makes the frontend swappable. If a
future Aurora client speaks this schema, it can drive any version
of the kernel that honours it.
"""

from __future__ import annotations

from typing import Any, Union

# Inbound kinds
EnvelopeInbound = Union[dict[str, Any]]  # narrowed by parse_envelope()


def envelope_event(topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Outbound: a bus event that just happened on the kernel side."""
    if not isinstance(topic, str) or not topic:
        raise ValueError("topic must be a non-empty string")
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    return {"type": "event", "topic": topic, "payload": payload}


def envelope_publish(topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Outbound helper (rarely used by the kernel itself, but handy in tests)."""
    return {"type": "publish", "topic": topic, "payload": payload}


def envelope_pong() -> dict[str, Any]:
    return {"type": "pong"}


def envelope_error(message: str) -> dict[str, Any]:
    return {"type": "error", "message": str(message)}


def parse_envelope(raw: Any) -> dict[str, Any]:
    """Validate and return an envelope received from the renderer.

    Accepts either a JSON string or a pre-parsed dict. Raises
    ``ValueError`` for unknown / missing ``type``, ``TypeError`` for
    non-dict input.
    """
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON envelope: {exc}") from exc
    if not isinstance(raw, dict):
        raise TypeError("envelope must be a JSON object")
    kind = raw.get("type")
    if not isinstance(kind, str) or not kind:
        raise ValueError("envelope missing 'type' field")
    if kind not in {"publish", "ping"}:
        raise ValueError(f"unknown inbound envelope type: {kind!r}")
    if kind == "publish":
        if not isinstance(raw.get("topic"), str) or not raw.get("topic"):
            raise ValueError("publish envelope missing 'topic'")
        if "payload" in raw and not isinstance(raw["payload"], dict):
            raise TypeError("publish envelope 'payload' must be an object")
    return raw
