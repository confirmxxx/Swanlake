"""`.swanlake-no-beacon` opt-out marker support (B13).

A zero-byte (or YAML-frontmatter-bearing) file at any directory's root
excludes that directory and all descendants from `sweep` and `deploy`.
Format -- two flavors:

  1. Empty file (or any content without `surfaces:` frontmatter):
     "skip everything below this directory."

  2. YAML frontmatter line `surfaces: [<id>, <id>]`:
     "skip only these specific surface-ids in this subtree."

The frontmatter is parsed with the same tiny stdlib parser the rest of
the package uses (no PyYAML). Any parse error falls back to the
zero-byte semantic ("skip everything") -- fail-closed: a malformed
opt-out is more conservative than treating it as not present.

Spec: §5 step 4 (deploy refusal), §9 R3 (sweep skip), N4 (hard NO).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

OPTOUT_FILENAME = ".swanlake-no-beacon"


@dataclass(frozen=True)
class OptOutMarker:
    """Parsed `.swanlake-no-beacon` payload."""

    path: Path
    # If empty, the marker excludes EVERYTHING under self.path's dir.
    # If non-empty, the marker excludes only these specific surface-ids.
    surface_filter: tuple[str, ...]

    @property
    def excludes_all(self) -> bool:
        return not self.surface_filter

    def excludes(self, surface_id: str) -> bool:
        if self.excludes_all:
            return True
        return surface_id in self.surface_filter


# Match a single `surfaces: [a, b, c]` line (with optional whitespace).
# Multi-line YAML lists are out of scope -- the opt-out file is meant
# to be one or two lines, not a full config.
_SURFACES_LINE_RE = re.compile(
    r"^\s*surfaces\s*:\s*\[([^\]]*)\]\s*$",
    flags=re.MULTILINE,
)


def _parse_marker_text(text: str) -> tuple[str, ...]:
    """Return the surface-id tuple, or () if the marker excludes everything."""
    m = _SURFACES_LINE_RE.search(text)
    if not m:
        return ()
    raw = m.group(1)
    items = [s.strip().strip("'\"") for s in raw.split(",")]
    return tuple(s for s in items if s)


def find_marker(start: Path, ceiling: Path | None = None) -> OptOutMarker | None:
    """Walk up from `start` looking for a `.swanlake-no-beacon` file.

    Stops at `ceiling` (inclusive) or filesystem root if ceiling is None.
    Returns the parsed marker for the first match; None if none found.

    The walk is bounded to 32 levels as a defense against pathological
    symlink loops. Beyond that depth, no realistic project layout exists.
    """
    cur = start.resolve() if start.exists() else start
    if cur.is_file():
        cur = cur.parent
    ceiling_resolved = ceiling.resolve() if ceiling and ceiling.exists() else None

    for _ in range(32):
        candidate = cur / OPTOUT_FILENAME
        if candidate.is_file():
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                # Unreadable opt-out -> fail-closed (treat as exclude-all).
                return OptOutMarker(path=candidate, surface_filter=())
            return OptOutMarker(
                path=candidate,
                surface_filter=_parse_marker_text(text),
            )

        if ceiling_resolved is not None and cur == ceiling_resolved:
            return None
        if cur.parent == cur:
            return None
        cur = cur.parent

    return None


def is_excluded(
    target: Path,
    surface_id: str,
    ceiling: Path | None = None,
) -> tuple[bool, OptOutMarker | None]:
    """Return (excluded, marker_or_none) for the given target + surface.

    `excluded == True` means: there is a `.swanlake-no-beacon` marker at
    or above `target`, and that marker excludes `surface_id`. The marker
    object is surfaced so callers can include the path in error messages
    (`opted out via <ancestor>/.swanlake-no-beacon`).
    """
    marker = find_marker(target, ceiling=ceiling)
    if marker is None:
        return False, None
    if marker.excludes(surface_id):
        return True, marker
    return False, marker


__all__ = [
    "OPTOUT_FILENAME",
    "OptOutMarker",
    "find_marker",
    "is_excluded",
]
