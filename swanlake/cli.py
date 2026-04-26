"""Swanlake unified CLI — argparse root with subparsers for the 9 v0.2 surfaces.

See docs/v0.2-unified-cli-spec.md section "CLI surface" for the locked grammar.

Subcommands:
    status, sync, verify, rotate, bench, doctor, init, adapt {cc,cma,sdk}

Top-level flags:
    --version, --state-root PATH, --quiet, --json
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from swanlake import __version__
from swanlake import state as _state
from swanlake.audit import AuditRecord
from swanlake.exit_codes import USAGE


SUBCOMMANDS = (
    "status",
    "sync",
    "verify",
    "rotate",
    "bench",
    "doctor",
    "init",
    "adapt",
    "coverage",
)

ADAPT_TARGETS = ("cc", "cma", "sdk")


def _common_flags_parser() -> argparse.ArgumentParser:
    """Parent parser carrying flags every subcommand inherits.

    Using `parents=[...]` on each subparser lets the operator write
    `swanlake status --json` (post-subcommand) AND `swanlake --json status`
    (pre-subcommand). Both forms parse identically.
    """
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--state-root",
        metavar="PATH",
        default=None,
        help="Override the state root (default: ~/.swanlake or $SWANLAKE_STATE_ROOT).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress non-error stdout output.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable output (per-subcommand).",
    )
    return p


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse tree.

    Two-level subparsers handle `swanlake adapt cc` cleanly without click/typer.
    """
    common = _common_flags_parser()
    # Common flags (--state-root, --quiet, --json) live ONLY on subparsers so
    # there is no top-level/sub-level override conflict. The spec annotates
    # --json as "per-subcommand" for exactly this reason. Operators write
    # `swanlake status --json`, not `swanlake --json status`.
    parser = argparse.ArgumentParser(
        prog="swanlake",
        description=(
            "Swanlake unified CLI -- composite posture command consolidating "
            "reconciler, canary, exfil, content-safety, closure, coverage, "
            "and bench dimensions."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"swanlake {__version__}",
    )

    sub = parser.add_subparsers(dest="cmd", metavar="<subcommand>")

    sub.add_parser(
        "status",
        help="Composite posture across 7 dimensions (exit 0/1/2).",
        parents=[common],
    )
    sync_p = sub.add_parser(
        "sync",
        help="Reconcile canon to managed surfaces (confirmation gated).",
        parents=[common],
    )
    sync_p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    verify_p = sub.add_parser(
        "verify",
        help="Check which surfaces still hold intact beacons.",
        parents=[common],
    )
    verify_p.add_argument(
        "--surface",
        metavar="NAME",
        default=None,
        help="Restrict the check to a single surface.",
    )
    verify_p.add_argument(
        "--since",
        metavar="ISO-8601",
        default=None,
        help="Skip surfaces whose verified_at is on or after this timestamp.",
    )
    rotate_p = sub.add_parser(
        "rotate",
        help="Rotate canary tokens across the registry (DESTRUCTIVE).",
        parents=[common],
    )
    rotate_p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    bench_p = sub.add_parser(
        "bench",
        help="Run the bench suite (--quick smoke, --full PyRIT+Garak).",
        parents=[common],
    )
    bench_grp = bench_p.add_mutually_exclusive_group()
    bench_grp.add_argument("--quick", action="store_true", help="One-minute smoke run.")
    bench_grp.add_argument("--full", action="store_true", help="Full PyRIT + Garak run (~1 h).")
    doctor_p = sub.add_parser(
        "doctor",
        help="Per-primitive health check with fix suggestions.",
        parents=[common],
    )
    doctor_p.add_argument(
        "--fix-suggestions",
        action="store_true",
        help="Append the exact remediation command to each non-passing row.",
    )
    init_p = sub.add_parser(
        "init",
        help="First-run bootstrap; idempotent.",
        parents=[common],
    )
    init_p.add_argument(
        "--add-surface",
        metavar="NAME",
        default=None,
        help="Register a single surface in coverage.json without re-running bootstrap.",
    )

    adapt_p = sub.add_parser(
        "adapt",
        help="Install Swanlake into a harness (cc, cma, sdk).",
        parents=[common],
    )
    adapt_sub = adapt_p.add_subparsers(dest="adapt_target", metavar="<target>")
    cc_p = adapt_sub.add_parser(
        "cc",
        help="Install Swanlake into the Claude Code harness.",
        parents=[common],
    )
    cc_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing.",
    )
    cc_p.add_argument(
        "--uninstall",
        action="store_true",
        help="Reverse a prior install via the manifest.",
    )
    cc_p.add_argument(
        "--cc-dir",
        metavar="PATH",
        default=None,
        help="Override the Claude Code dir (default: ~/.claude).",
    )
    cma_p = adapt_sub.add_parser(
        "cma",
        help="Install Beacon Part A + zones into a CMA project.",
        parents=[common],
    )
    cma_p.add_argument(
        "--project",
        metavar="PATH",
        required=True,
        help="Path to the CMA project root.",
    )
    cma_p.add_argument("--dry-run", action="store_true")
    cma_p.add_argument("--uninstall", action="store_true")
    cma_p.add_argument(
        "--cma-glob",
        default="cmas/*.md",
        help="Glob (relative to --project) selecting CMA definition files.",
    )
    cma_p.add_argument(
        "--zones",
        default=None,
        help="Path to zones.yaml (default: <project>/zones.yaml).",
    )
    cma_p.add_argument(
        "--tool-config-glob",
        default="cmas/*.tool-config.yaml",
        help="Glob selecting per-CMA tool-config files.",
    )
    cma_p.add_argument(
        "--reflex-glob",
        default="**/reflex*.py:**/hot_path*.py",
        help="Colon-separated globs for reflex/hot-path AST purity check.",
    )
    sdk_p = adapt_sub.add_parser(
        "sdk",
        help="(stub) Install Swanlake into the SDK harness -- v0.3.",
        parents=[common],
    )
    # No SDK-specific args; the stub handler ignores everything.
    del sdk_p

    coverage_p = sub.add_parser(
        "coverage",
        help="Surface inventory builder + browser.",
        parents=[common],
    )
    coverage_sub = coverage_p.add_subparsers(
        dest="coverage_op", metavar="<op>"
    )
    coverage_sub.add_parser(
        "scan",
        help="Walk projects + deployment-map; rebuild coverage.json.",
        parents=[common],
    )
    coverage_sub.add_parser(
        "list",
        help="Print the current coverage.json (no scan).",
        parents=[common],
    )

    return parser


def _stub(name: str, quiet: bool = False) -> int:
    """Placeholder handler for subcommands not implemented in this build slice."""
    if not quiet:
        print(f"swanlake {name}: not implemented in this build slice")
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    """Route a parsed Namespace to its subcommand handler.

    Returns the subcommand's exit code. Returns USAGE (2) if no subcommand.
    """
    cmd = args.cmd
    if cmd is None:
        # No subcommand provided. Print usage to stderr and exit 2.
        build_parser().print_usage(sys.stderr)
        return USAGE

    if cmd == "status":
        from swanlake.commands import status as status_cmd
        return status_cmd.run(args)
    if cmd == "sync":
        from swanlake.commands import sync as sync_cmd
        return sync_cmd.run(args)
    if cmd == "verify":
        from swanlake.commands import verify as verify_cmd
        return verify_cmd.run(args)
    if cmd == "rotate":
        return _stub("rotate", args.quiet)
    if cmd == "bench":
        from swanlake.commands import bench as bench_cmd
        return bench_cmd.run(args)
    if cmd == "doctor":
        from swanlake.commands import doctor as doctor_cmd
        return doctor_cmd.run(args)
    if cmd == "init":
        from swanlake.commands import init as init_cmd
        return init_cmd.run(args)
    if cmd == "adapt":
        from swanlake.commands import adapt as adapt_cmd
        return adapt_cmd.run(args)
    if cmd == "coverage":
        from swanlake.commands import coverage as coverage_cmd
        return coverage_cmd.run(args)

    # Defensive: unknown subcommand reached dispatch (should be caught by argparse).
    print(f"swanlake: unknown subcommand {cmd!r}", file=sys.stderr)
    return USAGE


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Parses argv, applies --state-root, audits, dispatches.

    Every invocation produces exactly one row in ~/.swanlake/audit.jsonl.
    The audit record captures the actual exit code returned by the
    subcommand handler (or USAGE/2 if dispatch never reached one).
    """
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)

    # Apply --state-root override before any subcommand handler runs so
    # the audit module writes to the requested location.
    resolved_root = _state.resolve_state_root(getattr(args, "state_root", None))
    _state.set_state_root(resolved_root)

    cmd = getattr(args, "cmd", None)
    if cmd == "adapt":
        subcmd = getattr(args, "adapt_target", None)
    elif cmd == "coverage":
        subcmd = getattr(args, "coverage_op", None)
    else:
        subcmd = None

    with AuditRecord(cmd=cmd, subcmd=subcmd, argv=raw_argv) as rec:
        exit_code = _dispatch(args)
        rec.set_exit(exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
