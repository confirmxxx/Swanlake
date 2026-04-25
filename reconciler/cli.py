"""swanlake-reconciler CLI.

Three subcommands today:
  --status   show per-surface sync state
  --sync     force re-sync (one-shot, useful after manual canon/ edits)
  --init     setup wizard for a fresh machine
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='swanlake-reconciler',
        description='Cross-surface autonomous sync for Swanlake.',
    )
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('--status', help='Show per-surface sync state')
    sub.add_parser('--sync', help='Force re-sync of all surfaces')
    sub.add_parser('--init', help='Setup wizard for a fresh machine')
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    if argv is not None and len(argv) == 0:
        parser.print_usage(sys.stderr)
        sys.exit(2)
    args = parser.parse_args(argv)
    # Subcommand dispatch — subcommands implemented in later tasks.
    if args.command == '--status':
        from reconciler.status import run_status
        return run_status()
    if args.command == '--sync':
        from reconciler.sync_vault import run_sync_all
        return run_sync_all()
    if args.command == '--init':
        from reconciler.init import run_init
        return run_init()
    parser.print_usage(sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    sys.exit(main())
