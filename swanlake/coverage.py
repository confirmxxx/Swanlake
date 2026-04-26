"""Coverage builder -- populates ~/.swanlake/coverage.json.

Spec MVP T10. Resolves the 8-vs-25 inventory drift by walking
~/projects/*/CLAUDE.md for beacon attribution markers and merging
with the deployment-map's mapped-surfaces dict.

Each entry in coverage.json carries a `source` field:
    "scanned"  -> only found in scan
    "mapped"   -> only present in deployment-map
    "both"     -> present in both
    "manual"   -> registered via swanlake init --add-surface

A surface is identified by its `beacon-attrib-<surface>-<8char>` token.
The 8-char tail is the per-surface hash that distinguishes it from
other deployments of the same surface ID; it is the literal we must
NEVER echo in any user-visible output (spec hard rule: no real-shaped
canary literals leaving the process).

The matching regex is anchored to the literal prefix `beacon-attrib-`
and validates the structure WITHOUT capturing the 8-char tail in any
group that flows into output. Only the surface-name slice is used.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from swanlake import state as _state


# Match `beacon-attrib-<surface>-<8 alphanum>`. The two named groups
# are `surface` (safe to surface) and `tail` (the 8-char hash, which
# we deliberately drop on the floor -- never logged, never returned).
_ATTRIB_RE = re.compile(
    r"beacon-attrib-(?P<surface>[a-z0-9-]+?)-(?P<tail>[A-Za-z0-9]{8})\b"
)


COVERAGE_FILENAME = "coverage.json"
DEFAULT_PROJECTS_ROOT = Path.home() / "projects"
DEFAULT_DEPLOYMENT_MAP = (
    Path.home() / "projects" / "DEFENSE-BEACON" / "deployment-map.json"
)


def _scan_file(path: Path) -> set[str]:
    """Return the set of surface IDs found in `path`. Tail bytes discarded."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    surfaces: set[str] = set()
    for m in _ATTRIB_RE.finditer(text):
        # Pull only the surface group; the 8-char tail is intentionally
        # dropped before any further processing.
        surfaces.add(m.group("surface"))
    return surfaces


def _scan_projects(
    projects_root: Path = DEFAULT_PROJECTS_ROOT,
) -> dict[str, list[str]]:
    """Walk projects_root for CLAUDE.md files; return {surface: [paths]}.

    Paths are returned but the canary tail is never included.
    """
    found: dict[str, list[str]] = {}
    if not projects_root.exists():
        return found
    # Glob a single level of project dirs; deeper CLAUDE.md files are
    # legitimate but not the canonical attribution surface.
    for cm in projects_root.glob("*/CLAUDE.md"):
        for surface in _scan_file(cm):
            found.setdefault(surface, []).append(str(cm))
    return found


def _load_deployment_map(path: Path) -> dict[str, list[str]]:
    """Read the deployment-map's surfaces dict. Return {} on any failure."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    surfaces = data.get("surfaces") or {}
    out: dict[str, list[str]] = {}
    if not isinstance(surfaces, dict):
        return out
    for k, v in surfaces.items():
        if isinstance(k, str) and isinstance(v, list):
            out[k] = [str(p) for p in v]
    return out


def _merge(
    scanned: dict[str, list[str]],
    mapped: dict[str, list[str]],
) -> dict[str, dict[str, Any]]:
    """Union scanned + mapped into the per-surface coverage entry shape."""
    merged: dict[str, dict[str, Any]] = {}
    all_keys = set(scanned) | set(mapped)
    for key in sorted(all_keys):
        in_s = key in scanned
        in_m = key in mapped
        if in_s and in_m:
            source = "both"
        elif in_s:
            source = "scanned"
        else:
            source = "mapped"
        # Path union (preserves order roughly: mapped first then any
        # extra scanned). Avoid duplicates without trashing order.
        seen: set[str] = set()
        paths: list[str] = []
        for p in mapped.get(key, []) + scanned.get(key, []):
            if p in seen:
                continue
            seen.add(p)
            paths.append(p)
        merged[key] = {"source": source, "paths": paths}
    return merged


def scan(
    projects_root: Path | None = None,
    deployment_map: Path | None = None,
    keep_existing_manual: bool = True,
) -> dict[str, Any]:
    """Rebuild coverage.json from a scan + deployment-map merge.

    Returns the full payload that was written. Paths are configurable for
    tests; defaults point at the operator's real layout.
    """
    pr = projects_root if projects_root is not None else DEFAULT_PROJECTS_ROOT
    dm = deployment_map if deployment_map is not None else DEFAULT_DEPLOYMENT_MAP

    scanned = _scan_projects(pr)
    mapped = _load_deployment_map(dm)
    merged = _merge(scanned, mapped)

    # Preserve manually-registered surfaces from a prior init --add-surface
    # so the scan doesn't blow them away.
    if keep_existing_manual:
        existing = _read_coverage()
        for k, v in existing.get("surfaces", {}).items():
            if k in merged:
                continue
            if isinstance(v, dict) and v.get("source") == "manual":
                merged[k] = v

    payload: dict[str, Any] = {
        "schema": 1,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "surfaces": merged,
    }
    _write_coverage(payload)
    return payload


def _read_coverage() -> dict[str, Any]:
    """Return the current coverage.json contents, or an empty payload."""
    p = _state.state_path(COVERAGE_FILENAME)
    if not p.exists():
        return {"schema": 1, "surfaces": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": 1, "surfaces": {}}


def _write_coverage(payload: dict[str, Any]) -> Path:
    """Atomic write of the coverage payload."""
    p = _state.state_path(COVERAGE_FILENAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    return p


def list_surfaces() -> dict[str, Any]:
    """Return the current coverage.json (without re-scanning)."""
    return _read_coverage()


__all__ = [
    "scan",
    "list_surfaces",
    "COVERAGE_FILENAME",
    "DEFAULT_PROJECTS_ROOT",
    "DEFAULT_DEPLOYMENT_MAP",
]
