"""`swanlake beacon sweep` -- find unbeaconed surfaces; emit a deployment plan.

Read-only. Walks the configured project roots + vault root + surfaces.yaml,
classifies each known/discovered surface as beaconed / partial / unbeaconed,
honors `.swanlake-no-beacon` opt-out markers. Stub.
"""
from __future__ import annotations

from swanlake.exit_codes import NOT_IMPLEMENTED
from swanlake.output import eprint


def run(args) -> int:
    eprint("swanlake beacon sweep: not implemented in this build slice")
    return NOT_IMPLEMENTED


__all__ = ["run"]
