"""`swanlake beacon verify` -- thin wrapper around `swanlake verify`.

Per spec D2: single source of truth for marker-grep stays in
swanlake.commands.verify.compute. This wrapper adds REMOTE-type
dispatch and per-surface scoping. Stub.
"""
from __future__ import annotations

from swanlake.exit_codes import NOT_IMPLEMENTED
from swanlake.output import eprint


def run(args) -> int:
    eprint("swanlake beacon verify: not implemented in this build slice")
    return NOT_IMPLEMENTED


__all__ = ["run"]
