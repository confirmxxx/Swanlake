"""`swanlake verify` -- check which surfaces still hold intact beacons.

Spec MVP T7. Reads ~/.swanlake/coverage.json (falls back to the
deployment-map). For each surface, verifies the file at each
registered path contains an attribution marker matching the surface
ID. The marker shape is `beacon-attrib-<surface>-<8 alphanum>` and
the verifier grep-matches it WITHOUT ever including the matched
literal in any user-visible output.

Per-surface status:
    intact     -> at least one path on disk contains a matching marker
    drifted    -> file present, no matching marker (canary scrubbed)
    missing    -> file path does not exist
    unreadable -> file present but read-error

Exit codes:
    0  all intact
    1  any drifted/missing/unreadable
    2  USAGE (no coverage source available)

Flags:
    --surface NAME   restrict the check to one surface
    --since DATE     skip surfaces whose coverage entry was verified
                     after DATE (ISO-8601 date or full ISO timestamp)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from swanlake import coverage as _cov
from swanlake import state as _state
from swanlake.exit_codes import ALARM, CLEAN, DRIFT, USAGE
from swanlake.output import eprint, print_json, print_line, print_table


def _marker_pattern(surface: str) -> re.Pattern[str]:
    """Compile a regex matching the attribution marker for `surface`.

    The marker is `beacon-attrib-<surface>-<8 alphanum>`. The surface
    name is regex-escaped because it can legitimately contain hyphens.
    """
    return re.compile(
        r"beacon-attrib-" + re.escape(surface) + r"-[A-Za-z0-9]{8}\b"
    )


def _check_path(surface: str, path: Path) -> str:
    """Return one of: intact / drifted / missing / unreadable."""
    if not path.exists():
        return "missing"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unreadable"
    if _marker_pattern(surface).search(text):
        # Critical: we return the *status only*. The match object is
        # discarded; the matched literal never enters the return value
        # or any log line.
        return "intact"
    return "drifted"


def _aggregate_statuses(per_path: list[str]) -> str:
    """Combine per-path statuses into a single per-surface status."""
    if not per_path:
        return "missing"
    if "intact" in per_path:
        # Any one good path is enough -- the surface is still attributed.
        return "intact"
    if "missing" in per_path and len(set(per_path)) == 1:
        return "missing"
    if "unreadable" in per_path:
        return "unreadable"
    return "drifted"


def _load_coverage_or_dmap() -> dict[str, dict[str, Any]] | None:
    """Return {surface: {paths: [...]}} from coverage.json or deployment-map.

    Returns None if neither source is available.
    """
    cov_payload = _cov.list_surfaces()
    surfaces = cov_payload.get("surfaces") or {}
    if surfaces:
        return surfaces
    # Fallback: synthesize surface entries from the deployment-map.
    dmap_path = _cov.DEFAULT_DEPLOYMENT_MAP
    if not dmap_path.exists():
        return None
    try:
        dmap = json.loads(dmap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    out: dict[str, dict[str, Any]] = {}
    for k, v in (dmap.get("surfaces") or {}).items():
        if isinstance(k, str) and isinstance(v, list):
            out[k] = {"source": "mapped", "paths": [str(p) for p in v]}
    return out or None


def _filter_since(
    surfaces: dict[str, dict[str, Any]], since: str | None
) -> dict[str, dict[str, Any]]:
    """Drop surfaces whose `verified_at` is on/after `since`."""
    if not since:
        return surfaces
    try:
        cutoff = datetime.fromisoformat(since)
    except ValueError:
        # Bad cutoff -> ignore the filter rather than nuking the whole
        # call. The operator sees a stderr hint.
        eprint(f"swanlake verify: --since {since!r} not parseable as ISO-8601; ignoring")
        return surfaces
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    out: dict[str, dict[str, Any]] = {}
    for k, v in surfaces.items():
        ts = v.get("verified_at") if isinstance(v, dict) else None
        if isinstance(ts, str):
            try:
                seen = datetime.fromisoformat(ts)
            except ValueError:
                seen = None
            if seen is not None:
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=timezone.utc)
                if seen >= cutoff:
                    continue
        out[k] = v
    return out


def compute(
    only_surface: str | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    surfaces = _load_coverage_or_dmap()
    if surfaces is None:
        return {"surfaces": [], "exit_code": USAGE,
                "error": "no coverage.json or deployment-map.json available"}

    if only_surface:
        surfaces = {k: v for k, v in surfaces.items() if k == only_surface}
        if not surfaces:
            return {"surfaces": [], "exit_code": USAGE,
                    "error": f"surface {only_surface!r} not in coverage"}

    surfaces = _filter_since(surfaces, since)

    rows: list[dict[str, Any]] = []
    worst = 0  # 0 clean, 1 drift
    for name in sorted(surfaces):
        entry = surfaces[name] or {}
        paths = entry.get("paths") or []
        per_path = [_check_path(name, Path(p)) for p in paths]
        agg = _aggregate_statuses(per_path)
        if agg != "intact":
            worst = max(worst, 1)
        rows.append({
            "surface": name,
            "status": agg,
            "n_paths": len(paths),
            # path_statuses is a per-position list -- safe to expose
            # because it carries no canary literal, only state words.
            "path_statuses": per_path,
        })
    exit_code = CLEAN if worst == 0 else DRIFT
    return {"surfaces": rows, "exit_code": exit_code}


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))
    only = getattr(args, "surface", None)
    since = getattr(args, "since", None)

    report = compute(only_surface=only, since=since)
    if "error" in report:
        eprint(f"swanlake verify: {report['error']}")
        return report["exit_code"]

    if json_out:
        print_json(report, quiet=quiet)
        return report["exit_code"]

    table_rows = [
        {
            "surface": r["surface"],
            "status": r["status"],
            "paths": r["n_paths"],
        }
        for r in report["surfaces"]
    ]
    print_table(
        table_rows, columns=("surface", "status", "paths"), quiet=quiet
    )
    if not quiet:
        word = "INTACT" if report["exit_code"] == CLEAN else "DRIFT"
        print_line(
            f"verify: {word}  [exit {report['exit_code']}]",
            quiet=False,
        )
    return report["exit_code"]


__all__ = ["run", "compute"]
