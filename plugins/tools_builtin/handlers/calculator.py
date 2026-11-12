"""Calculator handler — safe math expression evaluator.

Uses Python's ``ast`` module to parse the expression and evaluates
only a whitelisted subset of AST nodes. No ``eval()`` on arbitrary
strings — this is the safe approach per AGENTS.md Rule 9.

Returns:
    ToolResult envelope::

        {"ok": True, "data": {"result": <number>, "expression": <str>}, "error": None}
"""

from __future__ import annotations

import ast
import logging
import math
import operator
from typing import Any

logger = logging.getLogger("aurora.tools.calculator")

# ---------------------------------------------------------------------------
# Whitelist of safe AST node types and operators
# ---------------------------------------------------------------------------

_SAFE_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Call, ast.Name,
    ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Pow, ast.USub, ast.UAdd,
)

_SAFE_OPS: dict[type, Any] = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
    ast.USub:     operator.neg,
    ast.UAdd:     operator.pos,
}

# Safe math functions accessible by name in expressions like sqrt(9)
_SAFE_NAMES: dict[str, Any] = {
    "abs":   abs,
    "round": round,
    "sqrt":  math.sqrt,
    "floor": math.floor,
    "ceil":  math.ceil,
    "log":   math.log,
    "log2":  math.log2,
    "log10": math.log10,
    "sin":   math.sin,
    "cos":   math.cos,
    "tan":   math.tan,
    "pi":    math.pi,
    "e":     math.e,
}


def _safe_eval(node: ast.AST) -> float:
    """Recursively evaluate a whitelisted AST node."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")

    if isinstance(node, ast.BinOp):
        if type(node.op) not in _SAFE_OPS:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        left  = _safe_eval(node.left)
        right = _safe_eval(node.right)
        result = _SAFE_OPS[type(node.op)](left, right)
        if isinstance(result, complex):
            raise ValueError("Complex numbers not supported")
        return float(result)

    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _SAFE_OPS:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        operand = _safe_eval(node.operand)
        return float(_SAFE_OPS[type(node.op)](operand))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only named function calls are allowed")
        func_name = node.func.id
        if func_name not in _SAFE_NAMES:
            raise ValueError(f"Function '{func_name}' is not allowed. Allowed: {list(_SAFE_NAMES.keys())}")
        # FIX: preserve int type for args to support functions like round(pi, 4)
        # where the second arg must be int, not float.
        raw_args = [_safe_eval(a) for a in node.args]
        typed_args = [int(a) if a == int(a) else a for a in raw_args]
        return float(_SAFE_NAMES[func_name](*typed_args))

    if isinstance(node, ast.Name):
        if node.id not in _SAFE_NAMES:
            raise ValueError(f"Name '{node.id}' is not allowed")
        return float(_SAFE_NAMES[node.id])

    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


async def handle(args: dict[str, Any]) -> dict[str, Any]:
    """Calculator tool entry point.

    Args:
        args: dict with key ``expression`` (str).

    Returns:
        ToolResult envelope.
    """
    expression = str(args.get("expression", "")).strip()
    if not expression:
        return {"ok": False, "data": None, "error": "expression is required"}

    logger.info("calculator evaluating: %r", expression)

    # Normalise Python-style power operator (already supported) but also
    # accept caret notation common in user input, e.g. "2^10" → "2**10"
    normalised = expression.replace("^", "**")

    try:
        tree = ast.parse(normalised, mode="eval")
    except SyntaxError as exc:
        return {"ok": False, "data": None, "error": f"Syntax error: {exc}"}

    # Validate that every node is in the whitelist before evaluating
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_NODES):
            return {
                "ok": False,
                "data": None,
                "error": f"Disallowed expression element: {type(node).__name__}",
            }

    try:
        result = _safe_eval(tree.body)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        return {"ok": False, "data": None, "error": str(exc)}

    # Present integer results without decimal point
    display = int(result) if result == int(result) else result
    logger.info("calculator result: %s = %s", expression, display)
    return {
        "ok":   True,
        "data": {"expression": expression, "result": display},
        "error": None,
    }
