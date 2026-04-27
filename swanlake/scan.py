"""Project scan -- audit per-project beacon + opt-out + CMA shape.

Spec: docs/v0.4-enforcement-spec.md punch-list E2.

Walks ~/projects/*/ (one level under projects-root by default) and emits
a per-project payload with:

    has_claude_md    -- project has a CLAUDE.md at its root
    has_beacon       -- CLAUDE.md contains the Defense Beacon header
    has_optout       -- a .swanlake-no-beacon marker exists at or above
    is_cma_shaped    -- project has a cmas/ directory at its root
    recommended_action -- one of:
        opted-out      -- has_optout is True (any other state)
        clean          -- has_claude_md and has_beacon
        scaffold-cc    -- no CLAUDE.md, no opt-out, not CMA-shaped
        scaffold-cma   -- no CLAUDE.md, no opt-out, CMA-shaped
        deploy-beacon  -- has_claude_md but no beacon
        none           -- no CLAUDE.md, no CMA shape (genuinely empty)

The scan is read-only: no state file is touched, no canary literal is
echoed (the beacon-presence test is a header-only string check, never
the per-surface tail).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from swanlake.commands.beacon import _optout
from swanlake.coverage import SKIP_DIRS


# Header-only sentinel. Matches the Beacon v1 header literal from
# defense-beacon/SPEC.md without parsing the per-surface tail. Never
# captures or echoes the 8-char canary suffix -- this is a presence
# test, not a registry read.
_BEACON_HEADER_SENTINEL = "<!-- DEFENSE BEACON v"

DEFAULT_PROJECTS_ROOT = Path.home() / "projects"


@dataclass(frozen=True)
class ProjectStatus:
    """Per-project scan result."""

    path: Path
    has_claude_md: bool
    has_beacon: bool
    has_optout: bool
    is_cma_shaped: bool
    recommended_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "has_claude_md": self.has_claude_md,
            "has_beacon": self.has_beacon,
            "has_optout": self.has_optout,
            "is_cma_shaped": self.is_cma_shaped,
            "recommended_action": self.recommended_action,
        }


def _has_beacon_header(claude_md: Path) -> bool:
    """True iff the file contains the Defense Beacon v1 header sentinel.

    Substring match against `_BEACON_HEADER_SENTINEL` only -- the per-surface
    8-char canary tail is intentionally never read into a Python string,
    let alone returned. Aligns with coverage.py's "tail dropped on the
    floor" discipline.
    """
    try:
        text = claude_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _BEACON_HEADER_SENTINEL in text


def _classify(
    project: Path,
    *,
    projects_root: Path,
) -> ProjectStatus:
    """Build the ProjectStatus payload for one project root."""
    claude_md = project / "CLAUDE.md"
    has_claude_md = claude_md.is_file()
    has_beacon = has_claude_md and _has_beacon_header(claude_md)
    is_cma_shaped = (project / "cmas").is_dir()

    # Opt-out walk -- check if any ancestor (up to projects_root) carries
    # a .swanlake-no-beacon marker. Surface-id is "*" because v0.4 scan
    # treats opt-out as project-wide; per-surface filters apply at the
    # beacon layer, not the audit layer.
    excluded, _marker = _optout.is_excluded(
        target=project,
        surface_id="*",
        ceiling=projects_root,
    )

    if excluded:
        action = "opted-out"
    elif has_claude_md and has_beacon:
        action = "clean"
    elif has_claude_md and not has_beacon:
        action = "deploy-beacon"
    elif not has_claude_md and is_cma_shaped:
        action = "scaffold-cma"
    elif not has_claude_md and not is_cma_shaped:
        # Project root with neither CLAUDE.md nor cmas/ -- the action
        # is recommended only if the operator wants to use Claude Code
        # in this dir. We surface "scaffold-cc" so `--filter actionable`
        # can pick it up; operators who don't want CC in this dir can
        # drop a .swanlake-no-beacon to silence the recommendation.
        action = "scaffold-cc"
    else:
        # Defensive fallback (current branches above are exhaustive).
        action = "none"

    return ProjectStatus(
        path=project,
        has_claude_md=has_claude_md,
        has_beacon=has_beacon,
        has_optout=excluded,
        is_cma_shaped=is_cma_shaped,
        recommended_action=action,
    )


def _iter_project_roots(
    projects_root: Path,
    *,
    include_nested: bool = False,
) -> Iterable[Path]:
    """Yield project root candidates under `projects_root`.

    Default: one level under projects_root (immediate child dirs).
    With include_nested=True: rglob for any dir that has a CLAUDE.md or
    a cmas/ subdir, skipping SKIP_DIRS.

    Both modes skip dot-prefixed dirs at the top level (.claude, .cache,
    .local would otherwise be reported as "scaffold-cc" actionable rows).
    """
    if not projects_root.is_dir():
        return
    if not include_nested:
        for child in sorted(projects_root.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if child.name in SKIP_DIRS:
                continue
            yield child
        return

    # Nested mode -- walk the full tree, surface any dir that looks
    # like a project root.
    seen: set[Path] = set()
    for path in sorted(projects_root.rglob("*")):
        if not path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name.startswith("."):
            continue
        # A "project root" candidate is a dir that has a CLAUDE.md or
        # a cmas/ subdir directly inside it.
        has_signal = (path / "CLAUDE.md").is_file() or (path / "cmas").is_dir()
        if not has_signal:
            continue
        if path in seen:
            continue
        seen.add(path)
        yield path


def scan(
    projects_root: Path | None = None,
    *,
    include_nested: bool = False,
) -> dict[str, Any]:
    """Walk projects_root and return the full scan payload.

    Payload shape:
        {
            "schema": 1,
            "projects_root": "<abs-path>",
            "projects": [ProjectStatus.to_dict(), ...],
            "summary": {
                "n_total": int,
                "n_actionable": int,   # deploy-beacon + scaffold-cc + scaffold-cma
                "n_clean": int,
                "n_optout": int,
                "n_cma": int,
            },
        }

    Read-only. No filesystem writes. The walk is bounded by SKIP_DIRS
    (vendored / cache trees) and by the include_nested flag.
    """
    pr = (projects_root if projects_root is not None
          else DEFAULT_PROJECTS_ROOT).expanduser()

    rows: list[ProjectStatus] = []
    for project in _iter_project_roots(pr, include_nested=include_nested):
        rows.append(_classify(project, projects_root=pr))

    actionable_actions = {"deploy-beacon", "scaffold-cc", "scaffold-cma"}
    n_total = len(rows)
    n_actionable = sum(1 for r in rows if r.recommended_action in actionable_actions)
    n_clean = sum(1 for r in rows if r.recommended_action == "clean")
    n_optout = sum(1 for r in rows if r.recommended_action == "opted-out")
    n_cma = sum(1 for r in rows if r.is_cma_shaped)

    return {
        "schema": 1,
        "projects_root": str(pr),
        "projects": [r.to_dict() for r in rows],
        "summary": {
            "n_total": n_total,
            "n_actionable": n_actionable,
            "n_clean": n_clean,
            "n_optout": n_optout,
            "n_cma": n_cma,
        },
    }


def filter_payload(
    payload: dict[str, Any],
    *,
    filter_mode: str = "all",
) -> dict[str, Any]:
    """Return a copy of `payload` with the projects list narrowed.

    Modes:
        "all"        -- no narrowing
        "actionable" -- only deploy-beacon / scaffold-cc / scaffold-cma rows
        "clean"      -- only "clean" rows

    The summary is recomputed against the narrowed list so totals stay
    coherent with the displayed rows.
    """
    if filter_mode not in ("all", "actionable", "clean"):
        return payload  # caller validates; defensive no-op

    if filter_mode == "all":
        return payload

    actionable_actions = {"deploy-beacon", "scaffold-cc", "scaffold-cma"}
    rows = payload.get("projects") or []
    if filter_mode == "actionable":
        rows = [r for r in rows if r.get("recommended_action") in actionable_actions]
    elif filter_mode == "clean":
        rows = [r for r in rows if r.get("recommended_action") == "clean"]

    out = dict(payload)
    out["projects"] = rows
    # Recompute summary against the narrowed list.
    n_total = len(rows)
    n_actionable = sum(1 for r in rows if r.get("recommended_action") in actionable_actions)
    n_clean = sum(1 for r in rows if r.get("recommended_action") == "clean")
    n_optout = sum(1 for r in rows if r.get("recommended_action") == "opted-out")
    n_cma = sum(1 for r in rows if r.get("is_cma_shaped"))
    out["summary"] = {
        "n_total": n_total,
        "n_actionable": n_actionable,
        "n_clean": n_clean,
        "n_optout": n_optout,
        "n_cma": n_cma,
    }
    return out


__all__ = [
    "DEFAULT_PROJECTS_ROOT",
    "ProjectStatus",
    "scan",
    "filter_payload",
]
