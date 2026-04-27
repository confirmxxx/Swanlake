"""``swanlake reconciler ack`` -- record an operator ack for a remote sync.

Architecture context
--------------------
The reconciler dim of ``swanlake status`` reads ``last-sync.json``,
which only the local sync engines (vault, claude_md) write. Remote
Routines (notion via the security-watchdog routine + Notion MCP) cannot
write to that file because they have no filesystem access to the
operator's machine. The result was a permanent ``notion: missing``
ALARM that was a false positive.

This subcommand lets the operator record a manual ack after they've
confirmed (or kicked off) a remote routine run. The status reader
folds the most recent ack per surface into its freshness calculation
so the alarm clears. Acks decay on the same windows as syncs, so a
forgotten ack does NOT permanently mute the alarm -- it just delays
the next red signal by ``FRESH_WINDOW`` (24h today).

Surface classification
----------------------
A surface qualifies for ``--all-remote`` if it's classified as
``remote`` (or its alias ``cloud``) in the ``[surfaces]`` table of
``~/.swanlake/config.toml``. Defaults: ``notion`` is remote;
``vault`` and ``claude_md`` are local.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any

from reconciler import acks as _acks
from swanlake.exit_codes import CLEAN, USAGE
from swanlake.output import eprint, print_json, print_line


def _format_record(ack: _acks.Ack) -> dict[str, Any]:
    return {
        "surface": ack.surface,
        "synced_at": ack.synced_at.isoformat(),
        "acked_at": ack.acked_at.isoformat(),
        "note": ack.note,
    }


def _do_ack(
    surface: str,
    when: datetime | None,
    note: str,
) -> _acks.Ack:
    """Single-surface ack with consistent error semantics."""
    return _acks.write_ack(surface, synced_at=when, note=note)


def _ack_all_remote(
    when: datetime | None,
    note: str,
) -> list[_acks.Ack]:
    """Ack every surface classified as remote in the active config.

    Returns the list of acks actually written. Empty when no surface
    is classified as remote (operator has carved them all to local).
    """
    out: list[_acks.Ack] = []
    for surface in _acks.remote_surfaces():
        out.append(_do_ack(surface, when, note))
    return out


def run(args: argparse.Namespace) -> int:
    """Entry from ``swanlake.cli`` dispatcher."""
    op = getattr(args, "reconciler_op", None)
    if op != "ack":
        eprint("swanlake reconciler: no subcommand given (try `ack --help`)")
        return USAGE

    json_out = bool(getattr(args, "json", False))
    quiet = bool(getattr(args, "quiet", False))
    note = getattr(args, "note", "") or ""

    raw_since = getattr(args, "since", None)
    when: datetime | None = None
    if raw_since:
        try:
            when = _acks.parse_timestamp(raw_since)
        except ValueError as e:
            eprint(f"swanlake reconciler ack: invalid --since timestamp: {e}")
            return USAGE

    surface = getattr(args, "surface", None)
    all_remote = bool(getattr(args, "all_remote", False))

    if all_remote and surface:
        eprint("swanlake reconciler ack: pass either SURFACE or --all-remote, not both")
        return USAGE
    if not all_remote and not surface:
        eprint("swanlake reconciler ack: surface name required (or use --all-remote)")
        return USAGE

    try:
        if all_remote:
            written = _ack_all_remote(when, note)
        else:
            written = [_do_ack(surface, when, note)]
    except _acks.UnknownSurface as e:
        eprint(f"swanlake reconciler ack: {e}")
        return USAGE

    payload = {"acked": [_format_record(a) for a in written]}

    if json_out:
        print_json(payload, quiet=quiet)
    else:
        if not written:
            print_line(
                "no surfaces classified as remote in config -- nothing to ack",
                quiet=quiet,
            )
        else:
            for ack in written:
                tail = f" -- {ack.note}" if ack.note else ""
                print_line(
                    f"acked {ack.surface}: synced_at={ack.synced_at.isoformat()}"
                    f" (recorded {ack.acked_at.isoformat()}){tail}",
                    quiet=quiet,
                )

    return CLEAN


__all__ = ["run"]
