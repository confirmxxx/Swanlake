"""`swanlake beacon list` -- print the surface-type matrix.

Read-only. Lists the 7 known surface types with their deploy method,
scope (LOCAL/REMOTE), and one example surface ID. Honors --json for
machine consumption.

The output is intentionally small: one row per type, not one row per
known surface. For surface-level inventory use `swanlake coverage list`
or `swanlake beacon sweep`.
"""
from __future__ import annotations

from typing import Any

from swanlake.commands.beacon._registry import SURFACE_TYPES
from swanlake.exit_codes import CLEAN
from swanlake.output import print_json, print_line, print_table


def _build_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in SURFACE_TYPES:
        rows.append({
            "type": t.type_id,
            "scope": t.scope,
            "deploy_method": t.deploy_method,
            "example": t.examples[0] if t.examples else "",
            "description": t.description,
        })
    return rows


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))

    rows = _build_rows()

    if json_out:
        payload = {
            "surfaces": [
                {
                    "type": t.type_id,
                    "scope": t.scope,
                    "deploy_method": t.deploy_method,
                    "examples": list(t.examples),
                    "description": t.description,
                }
                for t in SURFACE_TYPES
            ],
        }
        print_json(payload, quiet=quiet)
        return CLEAN

    print_table(
        rows,
        columns=("type", "scope", "deploy_method", "example", "description"),
        quiet=quiet,
    )
    if not quiet:
        print_line(f"{len(rows)} surface types known", quiet=False)
    return CLEAN


__all__ = ["run"]
