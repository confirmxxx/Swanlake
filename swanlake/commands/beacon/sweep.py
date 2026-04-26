"""`swanlake beacon sweep` -- inventory surfaces; emit a deployment plan.

Read-only by default. Walks:
  - coverage.json (existing inventory of LOCAL beaconed surfaces)
  - surfaces.yaml (operator's full registry)
  - the project tree under ~/projects/ for any CLAUDE.md not yet in
    coverage.json, so newly created project files surface as unbeaconed
    rather than silently disappearing

Each surface is classified:
  - beaconed       : LOCAL file on disk carries an intact attribution marker
  - partial        : LOCAL file present but marker shape is malformed (per spec)
  - unbeaconed     : LOCAL surface known but no marker on disk
  - remote-pending : REMOTE surface known to the registry; verify via checklist
  - skipped-by-optout : `.swanlake-no-beacon` marker in an ancestor dir

Exit codes:
  0  no LOCAL surfaces are unbeaconed/partial
  1  any LOCAL surface is unbeaconed or partial (drift signal)

`--scope {local,remote,all}` filters which surface types to report on.
`--no-coverage-write` suppresses the coverage.json update.

Spec section 3 (matrix), §6 step 6 (history append).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from swanlake import coverage as _cov
from swanlake import state as _state
from swanlake.commands.beacon import _history, _optout
from swanlake.commands.beacon._registry import (
    SCOPE_LOCAL,
    SCOPE_REMOTE,
    SURFACE_TYPES,
    get_type,
    infer_type,
)
from swanlake.commands.beacon._surfaces import (
    SurfaceSpec,
    discover_surfaces_yaml,
    load_surfaces,
)
from swanlake.exit_codes import CLEAN, DRIFT
from swanlake.output import eprint, print_json, print_line, print_table


# Marker shape: presence of the per-surface attribution literal AND the
# DEFENSE BEACON v\d+ header. A file with only one is `partial`.
_HEADER_RE = re.compile(r"<!--\s*DEFENSE BEACON v\d+", flags=re.IGNORECASE)


def _attrib_re(surface_id: str) -> re.Pattern[str]:
    return re.compile(
        r"beacon-attrib-" + re.escape(surface_id) + r"-[A-Za-z0-9]{8}\b"
    )


def _classify_local_path(path: Path, surface_id: str) -> str:
    """Return one of: beaconed, partial, unbeaconed, missing, unreadable."""
    if not path.exists():
        return "missing"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unreadable"
    has_header = bool(_HEADER_RE.search(text))
    has_attrib = bool(_attrib_re(surface_id).search(text))
    if has_header and has_attrib:
        return "beaconed"
    if has_header or has_attrib:
        return "partial"
    return "unbeaconed"


def _surface_target_paths(surface_id: str) -> list[Path]:
    """Return registered LOCAL paths for `surface_id` from coverage.json."""
    cov = _cov.list_surfaces()
    surfaces = cov.get("surfaces") or {}
    entry = surfaces.get(surface_id) or {}
    paths = entry.get("paths") or []
    return [Path(p) for p in paths if isinstance(p, str)]


def _collect_known_surfaces() -> dict[str, dict[str, Any]]:
    """Union of (coverage.json, surfaces.yaml) keyed by surface-id.

    Each value carries:
      - source: "coverage" | "surfaces.yaml" | "both"
      - type_id: per registry inference
      - paths: list[str] (from coverage; empty for surfaces.yaml-only)
      - target: optional explicit target identifier (from surfaces.yaml)
    """
    out: dict[str, dict[str, Any]] = {}

    cov = _cov.list_surfaces()
    for sid, entry in (cov.get("surfaces") or {}).items():
        if not isinstance(sid, str):
            continue
        paths = (entry or {}).get("paths") or []
        out[sid] = {
            "source": "coverage",
            "type_id": infer_type(sid),
            "paths": [str(p) for p in paths if isinstance(p, str)],
            "target": None,
        }

    # Try to locate a surfaces.yaml for the operator. Best-effort: an
    # absent file is fine (sweep falls back to coverage-only).
    surfaces_path: Path | None = None
    try:
        from swanlake import _compat
        repo_root = _compat.find_repo_root()
        surfaces_path = discover_surfaces_yaml(repo_root)
    except Exception:
        surfaces_path = discover_surfaces_yaml(None)

    if surfaces_path is not None:
        try:
            specs = load_surfaces(surfaces_path)
        except OSError:
            specs = []
        for spec in specs:
            if spec.surface_id in out:
                out[spec.surface_id]["source"] = "both"
                # Prefer explicit type if surfaces.yaml carries one.
                if spec.type_id != out[spec.surface_id]["type_id"]:
                    out[spec.surface_id]["type_id"] = spec.type_id
                if spec.target:
                    out[spec.surface_id]["target"] = spec.target
            else:
                out[spec.surface_id] = {
                    "source": "surfaces.yaml",
                    "type_id": spec.type_id,
                    "paths": [],
                    "target": spec.target,
                }

    return out


def _discover_unregistered_local(known: set[str]) -> dict[str, list[str]]:
    """Walk ~/projects/ for CLAUDE.md attribution markers not in `known`.

    Reuses the existing _scan_projects() from swanlake.coverage so the
    skip-dir / vendored-tree logic stays single-sourced.
    """
    scanned = _cov._scan_projects()
    return {sid: paths for sid, paths in scanned.items() if sid not in known}


def _aggregate_local_status(per_path: list[str]) -> str:
    """Combine per-path classifications into a per-surface status."""
    if not per_path:
        return "unbeaconed"
    if "beaconed" in per_path:
        return "beaconed"
    if "partial" in per_path:
        return "partial"
    if all(s == "missing" for s in per_path):
        return "missing"
    return "unbeaconed"


def compute(scope: str = "all") -> dict[str, Any]:
    """Build the sweep report. No filesystem writes.

    Returns the structured payload with `unbeaconed`, `beaconed`,
    `summary`, and the proposed exit code.
    """
    known = _collect_known_surfaces()

    # Add discovered LOCAL surfaces (CLAUDE.md found on disk but not
    # registered anywhere yet). Type defaults to claude-md.
    if scope in ("local", "all"):
        for sid, paths in _discover_unregistered_local(set(known)).items():
            known[sid] = {
                "source": "scanned",
                "type_id": "claude-md",
                "paths": paths,
                "target": None,
            }

    rows_beaconed: list[dict[str, Any]] = []
    rows_unbeaconed: list[dict[str, Any]] = []
    rows_partial: list[dict[str, Any]] = []
    rows_remote: list[dict[str, Any]] = []
    rows_optout: list[dict[str, Any]] = []

    for sid in sorted(known):
        meta = known[sid]
        type_id = meta["type_id"]
        st = get_type(type_id)
        if st is None:
            continue

        # Scope filter.
        if scope == "local" and not st.is_local:
            continue
        if scope == "remote" and not st.is_remote:
            continue

        paths = [Path(p) for p in meta["paths"]]

        if st.is_local:
            # Per-path opt-out check. The first ancestor marker that
            # excludes this surface short-circuits the path entirely.
            excluded_path: Path | None = None
            optout_marker_path: str | None = None
            for p in paths:
                excluded, marker = _optout.is_excluded(p, sid)
                if excluded and marker is not None:
                    excluded_path = p
                    optout_marker_path = str(marker.path)
                    break
            if excluded_path is not None:
                rows_optout.append({
                    "surface": sid,
                    "type": type_id,
                    "target": str(excluded_path),
                    "marker": optout_marker_path,
                })
                continue

            # Classify each path; aggregate.
            per_path = [_classify_local_path(p, sid) for p in paths] if paths else []
            agg = _aggregate_local_status(per_path)
            if agg == "beaconed":
                rows_beaconed.append({
                    "surface": sid,
                    "type": type_id,
                    "target": str(paths[0]) if paths else "",
                    "n_paths": len(paths),
                })
            elif agg == "partial":
                rows_partial.append({
                    "surface": sid,
                    "type": type_id,
                    "target": str(paths[0]) if paths else "",
                    "n_paths": len(paths),
                })
            else:
                rows_unbeaconed.append({
                    "surface": sid,
                    "type": type_id,
                    "target": str(paths[0]) if paths else "(no path registered)",
                    "n_paths": len(paths),
                })
        else:
            # REMOTE surfaces: sweep cannot verify without a credential.
            # We surface them as "remote-pending" -- the operator runs
            # `swanlake beacon checklist` to deploy and `swanlake beacon
            # verify --surface <id>` to confirm.
            rows_remote.append({
                "surface": sid,
                "type": type_id,
                "target": meta.get("target") or "(see checklist)",
            })

    n_local_beaconed = len(rows_beaconed)
    n_local_unbeaconed = len(rows_unbeaconed) + len(rows_partial)
    n_remote = len(rows_remote)
    n_optout = len(rows_optout)

    payload: dict[str, Any] = {
        "scope": scope,
        "beaconed": rows_beaconed,
        "unbeaconed": rows_unbeaconed,
        "partial": rows_partial,
        "remote_pending": rows_remote,
        "skipped_by_optout": rows_optout,
        "summary": {
            "n_beaconed": n_local_beaconed,
            "n_unbeaconed": len(rows_unbeaconed),
            "n_partial": len(rows_partial),
            "n_remote_pending": n_remote,
            "n_skipped_by_optout": n_optout,
            "n_total": (
                n_local_beaconed
                + n_local_unbeaconed
                + n_remote
                + n_optout
            ),
        },
        "exit_code": DRIFT if n_local_unbeaconed > 0 else CLEAN,
    }
    return payload


def _maybe_update_coverage(payload: dict[str, Any]) -> None:
    """Persist any newly-discovered surfaces into coverage.json.

    Re-uses the same atomic write path as swanlake.coverage._write_coverage
    so the sweep cannot torn-write the inventory.
    """
    cov = _cov.list_surfaces()
    surfaces = cov.setdefault("surfaces", {})
    changed = False
    for row in (
        payload.get("beaconed", [])
        + payload.get("unbeaconed", [])
        + payload.get("partial", [])
    ):
        sid = row["surface"]
        if sid not in surfaces:
            surfaces[sid] = {
                "source": "scanned-by-beacon",
                "type": row.get("type"),
                "paths": [row["target"]] if row.get("target") else [],
            }
            changed = True
        else:
            entry = surfaces[sid]
            if isinstance(entry, dict) and not entry.get("type"):
                entry["type"] = row.get("type")
                changed = True
    if changed:
        cov["scanned_at"] = datetime.now(timezone.utc).isoformat()
        _cov._write_coverage(cov)


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))
    scope = getattr(args, "scope", "all")
    no_cov_write = bool(getattr(args, "no_coverage_write", False))

    payload = compute(scope=scope)

    if not no_cov_write:
        try:
            _maybe_update_coverage(payload)
        except OSError as e:
            eprint(f"swanlake beacon sweep: could not update coverage.json: {e}")

    # History row: best-effort.
    try:
        _history.append({
            "op": "sweep",
            "surface": None,
            "type": None,
            "method": None,
            "outcome": "scanned",
            "summary": payload["summary"],
        })
    except Exception:
        pass

    if json_out:
        print_json(payload, quiet=quiet)
        return payload["exit_code"]

    # Human-readable: four small tables (unbeaconed first since that's the
    # action-needed bucket).
    if payload["unbeaconed"]:
        print_line("UNBEACONED (LOCAL, ready to deploy):", quiet=quiet)
        print_table(
            payload["unbeaconed"],
            columns=("surface", "type", "target"),
            quiet=quiet,
        )
    if payload["partial"]:
        print_line("\nPARTIAL (LOCAL, repair manually before deploy):", quiet=quiet)
        print_table(
            payload["partial"],
            columns=("surface", "type", "target"),
            quiet=quiet,
        )
    if payload["remote_pending"]:
        print_line("\nREMOTE-PENDING (paste-checklist required):", quiet=quiet)
        print_table(
            payload["remote_pending"],
            columns=("surface", "type", "target"),
            quiet=quiet,
        )
    if payload["skipped_by_optout"]:
        print_line("\nSKIPPED-BY-OPTOUT:", quiet=quiet)
        print_table(
            payload["skipped_by_optout"],
            columns=("surface", "type", "target", "marker"),
            quiet=quiet,
        )
    if payload["beaconed"]:
        print_line("\nBEACONED (LOCAL, intact):", quiet=quiet)
        print_table(
            payload["beaconed"],
            columns=("surface", "type", "target"),
            quiet=quiet,
        )

    s = payload["summary"]
    word = "DRIFT" if payload["exit_code"] == DRIFT else "CLEAN"
    print_line(
        f"\nsweep: {word}  beaconed={s['n_beaconed']}  "
        f"unbeaconed={s['n_unbeaconed']}  partial={s['n_partial']}  "
        f"remote-pending={s['n_remote_pending']}  "
        f"opted-out={s['n_skipped_by_optout']}  [exit {payload['exit_code']}]",
        quiet=quiet,
    )
    return payload["exit_code"]


__all__ = ["run", "compute"]
