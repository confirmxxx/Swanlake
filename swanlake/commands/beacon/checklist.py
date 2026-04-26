"""`swanlake beacon checklist` -- emit paste-checklist for REMOTE surfaces.

Default output: stdout. `--out FILE` writes mode 0600 with a stderr warning.
Stub.
"""
from __future__ import annotations

from swanlake.exit_codes import NOT_IMPLEMENTED
from swanlake.output import eprint


def run(args) -> int:
    eprint("swanlake beacon checklist: not implemented in this build slice")
    return NOT_IMPLEMENTED


__all__ = ["run"]
