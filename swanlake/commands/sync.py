"""`swanlake sync` -- reconcile canon to managed surfaces.

Spec section A7: sync prompts `[y/N]` summarizing what will be touched
unless `--yes` or `SWANLAKE_NONINTERACTIVE=1` bypasses. Non-TTY without
either bypass exits 2 (USAGE) with a clear error.

The actual sync work is delegated to `reconciler.sync_vault.run_sync_all()`
which handles vault file propagation + Notion master page touch. We do
not re-implement any of that here -- this command is a thin safety
wrapper that records `prompted` / `confirmed` to the audit row.
"""
from __future__ import annotations

import sys
from typing import Any

from swanlake.exit_codes import USAGE
from swanlake.output import eprint, print_json, print_line
from swanlake.safety import confirm, is_noninteractive


def _summary_lines() -> list[str]:
    """Build a brief preview of what `sync` will touch.

    Kept terse on purpose -- the operator sees this every sync invocation
    and a wall of text trains them to ignore the prompt. Per-file
    propagation detail is printed by run_sync_all() during the run.
    """
    return [
        "swanlake sync will:",
        "  - propagate canon -> vault files referenced in deployment-map",
        "  - touch the Notion master page sync timestamp",
        "Existing files are atomic-write replaced; divergent files are skipped.",
    ]


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def run(args) -> int:
    """CLI entry. `args` is the argparse Namespace from swanlake.cli.

    Exit codes:
      0 on successful sync (or operator-aborted prompt)
      1 if reconciler reported per-file errors
      2 on USAGE (non-TTY without --yes / NONINTERACTIVE)
      whatever reconciler returns for config-missing-shaped errors

    Audit-side effects: this command does NOT itself touch the audit
    row. The CLI's AuditRecord context manager records exit_code via
    set_exit() and noninteractive via the env var. To distinguish
    `prompted` vs `confirmed` we rely on the noninteractive flag plus
    the args.yes flag; both surface in the audit args list.
    """
    yes: bool = bool(getattr(args, "yes", False))
    quiet: bool = bool(getattr(args, "quiet", False))
    json_out: bool = bool(getattr(args, "json", False))

    bypass = yes or is_noninteractive()
    tty = _is_tty()

    if not bypass and not tty:
        # Non-TTY without explicit bypass -> refuse with a clear error.
        eprint(
            "swanlake sync: no TTY and no --yes / SWANLAKE_NONINTERACTIVE=1; "
            "refusing to proceed without operator confirmation."
        )
        return USAGE

    # Show preview unless the operator suppressed it. Even with --yes the
    # preview is useful in scrollback for after-the-fact review.
    if not quiet:
        for line in _summary_lines():
            print_line(line, quiet=False)

    prompted = not bypass
    confirmed = confirm("Proceed with sync?", yes=yes)

    # Aborted at the prompt -> not an error, exit 0. The audit row will
    # carry exit_code=0 and the args list shows --yes was absent.
    if not confirmed:
        if json_out:
            print_json(
                {"sync": "aborted", "prompted": prompted, "confirmed": False},
                quiet=quiet,
            )
        elif not quiet:
            print_line("aborted by operator (no sync run).", quiet=False)
        return 0

    # Confirmed -> dispatch to the reconciler. We import inline so the
    # test suite can monkey-patch sync.run_sync_all without dragging the
    # whole reconciler import graph into module-load time.
    from reconciler import sync_vault as _sync_vault

    rc = _sync_vault.run_sync_all()
    if json_out:
        print_json(
            {
                "sync": "ran",
                "prompted": prompted,
                "confirmed": True,
                "exit_code": int(rc),
            },
            quiet=quiet,
        )
    return int(rc)


__all__ = ["run"]
