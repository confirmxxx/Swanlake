"""`swanlake beacon list` -- print the surface-type matrix.

Read-only. Lists the 7 known surface types with their deploy method and
example surface IDs. Stub: returns NOT_IMPLEMENTED until B1 lands.
"""
from __future__ import annotations

from swanlake.exit_codes import NOT_IMPLEMENTED
from swanlake.output import eprint


def run(args) -> int:
    eprint("swanlake beacon list: not implemented in this build slice")
    return NOT_IMPLEMENTED


__all__ = ["run"]
