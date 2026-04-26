"""Harness adapters -- one module per target.

Spec section A8: an Adapter has install/uninstall/verify/list_surfaces.
Concrete implementations live in this package: cc.py (Claude Code),
cma.py (Claude Managed Agents), sdk.py (stub for v0.3).

The top-level dispatcher `run(args)` routes by args.adapt_target.
"""
from __future__ import annotations

from swanlake.exit_codes import USAGE
from swanlake.output import eprint


def run(args) -> int:
    """Dispatch `swanlake adapt <target>` to the right adapter module."""
    target = getattr(args, "adapt_target", None)
    if target == "cc":
        from swanlake.commands.adapt import cc
        return cc.run(args)
    if target == "cma":
        from swanlake.commands.adapt import cma
        return cma.run(args)
    if target == "sdk":
        from swanlake.commands.adapt import sdk
        return sdk.run(args)
    eprint(
        "swanlake adapt: missing target. Use 'cc', 'cma', or 'sdk'."
    )
    return USAGE


__all__ = ["run"]
