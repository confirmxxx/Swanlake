"""`swanlake coverage` -- scan / list the surface inventory.

Spec MVP T10. Subcommands:
    swanlake coverage scan   -- rebuild coverage.json from sources
    swanlake coverage list   -- print current coverage.json

The output NEVER includes the 8-char canary tail; only the surface
identifier and the file paths the surface lives in. Paths come from
the deployment-map and the operator's filesystem -- they are not
secrets, but the canary tail is.
"""
from __future__ import annotations

from typing import Any

from swanlake import coverage as _cov
from swanlake.exit_codes import CLEAN
from swanlake.output import print_json, print_line, print_table


def _render_table(payload: dict[str, Any], quiet: bool) -> None:
    surfaces = payload.get("surfaces") or {}
    rows = []
    for name in sorted(surfaces):
        entry = surfaces[name] or {}
        paths = entry.get("paths") or []
        # Show count + first path; full path list goes to --json. This
        # keeps the table narrow without losing high-signal info.
        first = paths[0] if paths else "(no paths)"
        rest = f" (+{len(paths) - 1} more)" if len(paths) > 1 else ""
        rows.append({
            "surface": name,
            "source": entry.get("source", "?"),
            "paths": f"{first}{rest}",
        })
    print_table(rows, columns=("surface", "source", "paths"), quiet=quiet)
    if not quiet:
        print_line(f"{len(rows)} surfaces tracked", quiet=False)


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))
    sub = getattr(args, "coverage_op", None)

    if sub == "scan":
        payload = _cov.scan()
        if json_out:
            print_json(payload, quiet=quiet)
        else:
            print_line(
                f"scanned -- {len(payload.get('surfaces') or {})} surfaces written"
                f" to {_cov._state.state_path(_cov.COVERAGE_FILENAME)}",
                quiet=quiet,
            )
        return CLEAN

    # Default and `list` both render the existing coverage.
    payload = _cov.list_surfaces()
    if json_out:
        print_json(payload, quiet=quiet)
    else:
        _render_table(payload, quiet=quiet)
    return CLEAN


__all__ = ["run"]
