"""`swanlake beacon deploy` -- LOCAL deploy with the 12-step safety machine.

Mutates one local file per invocation. REMOTE surfaces are refused with a
hint to run `swanlake beacon checklist`. Stub.
"""
from __future__ import annotations

from swanlake.exit_codes import NOT_IMPLEMENTED
from swanlake.output import eprint


def run(args) -> int:
    eprint("swanlake beacon deploy: not implemented in this build slice")
    return NOT_IMPLEMENTED


__all__ = ["run"]
