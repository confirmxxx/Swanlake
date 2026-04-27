"""Swanlake unified CLI — argparse root with subparsers for the 9 v0.2 surfaces.

See docs/v0.2-unified-cli-spec.md section "CLI surface" for the locked grammar.

Subcommands:
    status, sync, verify, rotate, bench, doctor, init, adapt {cc,cma,sdk}

Top-level flags:
    --version, --state-root PATH, --quiet, --json
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from swanlake import __version__
from swanlake import install_marker as _install_marker
from swanlake import state as _state
from swanlake.audit import AuditRecord
from swanlake.exit_codes import USAGE


def _maybe_warn_install_drift(argv: Sequence[str]) -> None:
    """Print the install-drift warning to stderr if the runtime source
    does not match ~/.swanlake/.install-marker.

    Spec: docs/v0.3.x-worktree-install-isolation-spec.md T2.

    Called BEFORE argparse so the warning still prints when argv
    triggers an early-exit action (notably `--version`, which
    SystemExits inside parse_args before _dispatch ever runs). The
    check is silent on:
      - missing marker (degrades-to-silent for pre-v0.3.x installs)
      - cross-interpreter mismatch (multi-python hosts share the dir)
      - --quiet anywhere in argv (no stderr noise in scripts)
      - SWANLAKE_NO_INSTALL_DRIFT_WARN=1 (CI / intentional drift)
      - any exception during the check itself (the warning must
        never crash the CLI, even on a corrupt marker)
    """
    if "--quiet" in argv:
        return
    if os.environ.get(_install_marker.DRIFT_WARN_ENV) == "1":
        return
    try:
        drift = _install_marker.check_drift()
    except Exception:  # noqa: BLE001 — never crash CLI on warning path
        return
    if drift.get("status") != "drift":
        return
    try:
        sys.stderr.write(_install_marker.format_drift_warning(drift))
    except OSError:
        # Closed/broken stderr (e.g. piped to a closed FD). Silently swallow.
        pass


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
    "beacon",
    "reconciler",
    "scan",
)

ADAPT_TARGETS = ("cc", "cma", "sdk")
BEACON_OPS = ("list", "sweep", "deploy", "checklist", "verify")
RECONCILER_OPS = ("ack",)


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
    sync_p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Preview canon -> vault file paths and Notion page IDs that "
            "would be touched without invoking the reconciler. Exit 0 "
            "always; safe in CI/cron."
        ),
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
    cc_p.add_argument(
        "--skill-only",
        action="store_true",
        help=(
            "Install (or uninstall) only the /swanlake slash-command "
            "skill; skip hook scripts and settings.json patching. Use "
            "when the operator runs their own production hooks and only "
            "wants the skill on top."
        ),
    )
    cc_p.add_argument(
        "--enable-session-nudge",
        action="store_true",
        help=(
            "Drop the v0.4 SessionStart advisory hook into "
            "~/.claude/hooks/ and wire it into settings.json's "
            "SessionStart bucket. The hook prints one stderr line on "
            "session start if the project has CLAUDE.md but no beacon "
            "attribution and no opt-out marker. Always exits 0."
        ),
    )
    cc_p.add_argument(
        "--disable-session-nudge",
        action="store_true",
        help=(
            "Reverse --enable-session-nudge: remove the SessionStart "
            "hook script and drop its settings.json entry. Manifest-aware."
        ),
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

    # beacon: sweep + deploy + checklist + verify (v0.3 spec).
    beacon_p = sub.add_parser(
        "beacon",
        help="Sweep + deploy beacons across LOCAL/REMOTE surfaces.",
        parents=[common],
    )
    beacon_sub = beacon_p.add_subparsers(
        dest="beacon_op", metavar="<op>"
    )
    beacon_sub.add_parser(
        "list",
        help="Print the surface-type matrix (read-only).",
        parents=[common],
    )
    sweep_p = beacon_sub.add_parser(
        "sweep",
        help="Find unbeaconed surfaces; emit a deployment plan (no writes).",
        parents=[common],
    )
    sweep_p.add_argument(
        "--scope",
        choices=("local", "remote", "all"),
        default="all",
        help="Restrict the sweep to LOCAL, REMOTE, or all surface types.",
    )
    sweep_p.add_argument(
        "--no-coverage-write",
        action="store_true",
        help="Do not update coverage.json with discovered surfaces.",
    )
    deploy_p = beacon_sub.add_parser(
        "deploy",
        help="LOCAL deploy to one surface (REMOTE prints checklist hint).",
        parents=[common],
    )
    deploy_p.add_argument(
        "surface",
        metavar="SURFACE",
        help="Surface ID to deploy (must exist in surfaces.yaml).",
    )
    deploy_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the 12-step plan without writing or backing up.",
    )
    deploy_p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (also honored: SWANLAKE_NONINTERACTIVE=1).",
    )
    checklist_p = beacon_sub.add_parser(
        "checklist",
        help="Emit a paste-checklist for REMOTE surfaces.",
        parents=[common],
    )
    checklist_p.add_argument(
        "--out",
        metavar="FILE",
        default=None,
        help="Write to FILE mode 0600 (default: stdout).",
    )
    checklist_p.add_argument(
        "--surface",
        metavar="NAME",
        default=None,
        help="Restrict the checklist to a single surface.",
    )
    checklist_p.add_argument(
        "--include",
        choices=("pending", "all"),
        default="pending",
        help="Include only pending REMOTE surfaces, or all of them.",
    )
    checklist_p.add_argument(
        "--remind-export-stale",
        metavar="DURATION",
        default=None,
        help=(
            "Warn on stderr if ~/.swanlake/routines-export.json mtime is older "
            "than DURATION (e.g. `30d`). Routines are export-only (D8); the "
            "operator must re-export periodically. Format: <int>(d|h|m)."
        ),
    )
    verify_p = beacon_sub.add_parser(
        "verify",
        help="Thin wrapper over `swanlake verify` with REMOTE-type dispatch.",
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
        help="Skip surfaces verified on or after this timestamp.",
    )

    # reconciler: ack subcommand for remote-only sync surfaces.
    # Lives on the unified CLI so operators have one entry point; the
    # bare ``swanlake-reconciler`` script keeps its own --status/--sync/--init
    # flags untouched for back-compat with the systemd timer.
    recon_p = sub.add_parser(
        "reconciler",
        help="Reconciler ops (today: ack remote-only sync surfaces).",
        parents=[common],
    )
    recon_sub = recon_p.add_subparsers(
        dest="reconciler_op", metavar="<op>"
    )
    ack_p = recon_sub.add_parser(
        "ack",
        help=(
            "Record an operator ack for a surface synced by a remote routine "
            "(e.g. notion). Without this, the reconciler dim shows a "
            "permanent `missing` ALARM because remote routines cannot "
            "write the local last-sync.json."
        ),
        parents=[common],
    )
    ack_p.add_argument(
        "surface",
        metavar="SURFACE",
        nargs="?",
        default=None,
        help="Surface name to ack (omit when using --all-remote).",
    )
    ack_p.add_argument(
        "--since",
        metavar="ISO-8601",
        default=None,
        help=(
            "Claimed time the remote sync actually happened (default: now). "
            "Accepts trailing Z or +00:00."
        ),
    )
    ack_p.add_argument(
        "--all-remote",
        action="store_true",
        help=(
            "Ack every surface classified as `remote` (or alias `cloud`) in "
            "~/.swanlake/config.toml [surfaces]. Defaults: notion is remote."
        ),
    )
    ack_p.add_argument(
        "--note",
        metavar="TEXT",
        default="",
        help="Optional free-text note recorded with the ack.",
    )

    # scan: per-project audit of beacon + opt-out + CMA shape (v0.4 L1).
    scan_p = sub.add_parser(
        "scan",
        help="Walk ~/projects/*; report per-project beacon / opt-out status.",
        parents=[common],
    )
    scan_p.add_argument(
        "--projects-root",
        metavar="PATH",
        default=None,
        help="Override the projects root (default: ~/projects).",
    )
    scan_p.add_argument(
        "--include-nested",
        action="store_true",
        help=(
            "Walk the full tree under projects-root, not just immediate "
            "children. Picks up monorepo / split-package layouts."
        ),
    )
    scan_p.add_argument(
        "--filter",
        choices=("all", "actionable", "clean"),
        default="all",
        help=(
            "Narrow the report: 'actionable' shows only deploy-beacon / "
            "scaffold-cc / scaffold-cma rows; 'clean' shows only fully "
            "beaconed projects."
        ),
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
    if cmd == "beacon":
        from swanlake.commands import beacon as beacon_cmd
        return beacon_cmd.run(args)
    if cmd == "reconciler":
        from swanlake.commands import reconciler as reconciler_cmd
        return reconciler_cmd.run(args)
    if cmd == "scan":
        from swanlake.commands import scan as scan_cmd
        return scan_cmd.run(args)

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

    # Drift check runs BEFORE parse_args because `--version` and
    # similar action='version' flags SystemExit during parse_args
    # (before _dispatch is reached). Placing the check earlier means
    # `swanlake --version` from a drifted install still warns.
    _maybe_warn_install_drift(raw_argv)

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
    elif cmd == "beacon":
        subcmd = getattr(args, "beacon_op", None)
    elif cmd == "reconciler":
        subcmd = getattr(args, "reconciler_op", None)
    else:
        subcmd = None

    with AuditRecord(cmd=cmd, subcmd=subcmd, argv=raw_argv) as rec:
        exit_code = _dispatch(args)
        rec.set_exit(exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
