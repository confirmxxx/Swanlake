"""`swanlake scan` -- per-project audit of beacon + opt-out + CMA shape.

Spec: docs/v0.4-enforcement-spec.md punch-list E3.

Walks ~/projects/*/ (or --projects-root PATH) and emits a per-project
table with the recommended action. Read-only -- exit 0 always.

Output shapes:
    default (table)  -- one row per project, columns:
        path  has_claude_md  has_beacon  has_optout  cma  recommended_action
    --json           -- the full payload from swanlake.scan.scan()

Filters:
    --filter all         -- no narrowing (default)
    --filter actionable  -- only deploy-beacon / scaffold-cc / scaffold-cma
    --filter clean       -- only "clean" rows
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from swanlake import scan as _scan
from swanlake.exit_codes import CLEAN
from swanlake.output import print_json, print_line, print_table


def _render_table(payload: dict[str, Any], quiet: bool) -> None:
    rows = payload.get("projects") or []
    table_rows = []
    for r in rows:
        table_rows.append({
            "path": r.get("path", ""),
            "has_claude_md": _bool(r.get("has_claude_md")),
            "has_beacon": _bool(r.get("has_beacon")),
            "has_optout": _bool(r.get("has_optout")),
            "cma": _bool(r.get("is_cma_shaped")),
            "recommended_action": r.get("recommended_action", ""),
        })
    columns = (
        "path",
        "has_claude_md",
        "has_beacon",
        "has_optout",
        "cma",
        "recommended_action",
    )
    print_table(table_rows, columns=columns, quiet=quiet)
    summary = payload.get("summary") or {}
    if not quiet:
        print_line(
            f"{summary.get('n_total', 0)} projects scanned -- "
            f"{summary.get('n_actionable', 0)} actionable, "
            f"{summary.get('n_clean', 0)} clean, "
            f"{summary.get('n_optout', 0)} opted-out, "
            f"{summary.get('n_cma', 0)} cma-shaped",
            quiet=False,
        )


def _bool(value: Any) -> str:
    """Render a bool as 'yes' / 'no' for table cells (less noisy than True/False)."""
    return "yes" if value else "no"


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))
    projects_root = getattr(args, "projects_root", None)
    include_nested = bool(getattr(args, "include_nested", False))
    filter_mode = getattr(args, "filter", "all") or "all"

    pr = Path(projects_root).expanduser() if projects_root else None
    payload = _scan.scan(projects_root=pr, include_nested=include_nested)
    payload = _scan.filter_payload(payload, filter_mode=filter_mode)

    if json_out:
        print_json(payload, quiet=quiet)
    else:
        _render_table(payload, quiet=quiet)

    # Read-only -- always exit CLEAN even if actionable rows exist.
    # The summary line tells the operator there's work to do; the
    # exit code stays 0 so scripted callers can grep stdout/stderr
    # without conflating "scan completed" with "everything OK".
    return CLEAN


__all__ = ["run"]
