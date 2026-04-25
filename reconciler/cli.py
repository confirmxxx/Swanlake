"""swanlake-reconciler CLI.

Three top-level flags (mutually exclusive, one required):
  --status   show per-surface sync state
  --sync     force re-sync (one-shot, useful after manual canon/ edits)
  --init     setup wizard for a fresh machine
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from reconciler import status as status_mod
from reconciler import sync_vault as sync_vault_mod
from reconciler import init as init_mod


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='swanlake-reconciler',
        description='Cross-surface autonomous sync for Swanlake.',
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--status', action='store_true',
                       help='Show per-surface sync state')
    group.add_argument('--sync', action='store_true',
                       help='Force re-sync of all surfaces')
    group.add_argument('--init', action='store_true',
                       help='Run the setup wizard for a fresh machine')
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    if argv is not None and len(argv) == 0:
        parser.print_usage(sys.stderr)
        sys.exit(2)
    args = parser.parse_args(argv)
    if args.status:
        return status_mod.run_status()
    if args.sync:
        return sync_vault_mod.run_sync_all()
    if args.init:
        return init_mod.run_init()
    parser.print_usage(sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    sys.exit(main())
