"""Hot-path order router for the fixture project.

This module is INTENTIONALLY clean: zero LLM imports, zero LLM calls.
The reflex-purity check inside `swanlake adapt cma` walks files
matching the reflex glob and refuses to register a CMA project that
has any LLM call inside a hot-path module.
"""
from __future__ import annotations

import time


def route_order(symbol: str, side: str, qty: int) -> dict:
    """Pure routing logic. No I/O, no LLM, no surprises."""
    if qty <= 0:
        return {"ok": False, "reason": "non-positive qty"}
    return {
        "ok": True,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "ts": time.time_ns(),
    }
