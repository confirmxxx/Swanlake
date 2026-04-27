"""Surfaces config loader -- reads `surfaces.yaml` (or .example) from the
operator's defense-beacon registry.

The format is the same one make-canaries.py understands: one surface-id
per line, `#` comments allowed, blank lines tolerated. Optionally, an
extended block syntax lets the operator annotate a surface with explicit
`type:` / `target:` fields:

    cms-project-alpha
    repo-foo:
      type: github-public
      target: owner/foo:README.md
    deploy-bar:
      type: vercel-env

The plain-id form remains the common case. Annotations are only needed
for surfaces whose type can't be inferred from the prefix (or whose
target identifier needs to be carried through to the checklist).

Stdlib-only parsing (no PyYAML dep). The annotated form uses the same
indent-aware mini-parser as swanlake.commands.adapt.cma; we re-implement
the small subset here because importing across command boundaries
creates a circular layer dependency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swanlake.commands.beacon._registry import infer_type, validate_surface_id


@dataclass(frozen=True)
class SurfaceSpec:
    """One row of the operator's surfaces.yaml."""

    surface_id: str
    type_id: str
    target: str | None = None
    extra: dict[str, Any] | None = None


# A line is one of:
#   plain-id:  <whitespace?><surface-id><whitespace?>           (no colon at all)
#   header:    <whitespace?><surface-id>:<whitespace?>          (colon, nothing after)
#   kv:        <leading-whitespace><key>:<whitespace>value      (only valid inside an open block)
_PLAIN_LINE_RE = re.compile(r"^([a-z0-9][a-z0-9-]*[a-z0-9])$")
_HEADER_RE = re.compile(r"^([a-z0-9][a-z0-9-]*[a-z0-9]):$")
_KV_RE = re.compile(r"^([a-z_][a-z0-9_-]*)\s*:\s*(.+?)$")


def parse_surfaces_text(text: str) -> list[SurfaceSpec]:
    """Parse `text` into an ordered list of SurfaceSpec.

    Tolerant: malformed lines are skipped. Validation of the surface-id
    grammar is delegated to validate_surface_id() so the same regex
    governs both make-canaries.py and the swanlake CLI surface ingest.
    """
    out: list[SurfaceSpec] = []
    current_id: str | None = None
    current_attrs: dict[str, Any] = {}

    def _flush():
        nonlocal current_id, current_attrs
        if current_id is None:
            return
        explicit_type = current_attrs.pop("type", None)
        if not isinstance(explicit_type, str):
            explicit_type = None
        target = current_attrs.pop("target", None)
        if target is not None and not isinstance(target, str):
            target = str(target)
        out.append(SurfaceSpec(
            surface_id=current_id,
            type_id=infer_type(current_id, explicit_type=explicit_type),
            target=target,
            extra=current_attrs.copy() if current_attrs else None,
        ))
        current_id = None
        current_attrs = {}

    for raw in text.splitlines():
        # Strip line-end comments first (`id  # note`).
        no_comment = raw.split("#", 1)[0].rstrip()
        if not no_comment.strip():
            continue

        # Detect indentation: if the original line had leading whitespace
        # AND there's an open block, this is a candidate kv continuation.
        had_indent = no_comment[:1] in (" ", "\t")
        stripped = no_comment.strip()

        # 1. Plain-id (no colon at all).
        m_plain = _PLAIN_LINE_RE.match(stripped)
        if m_plain and ":" not in stripped:
            sid = m_plain.group(1)
            # Indented plain-id with no open block is still a top-level id.
            # Indented plain-id WITH an open block would be ambiguous; we
            # close the open block and emit the new surface.
            _flush()
            if validate_surface_id(sid):
                current_id = sid
                current_attrs = {}
                _flush()
            continue

        # 2. Header (id followed by colon, nothing after).
        m_header = _HEADER_RE.match(stripped)
        if m_header:
            _flush()
            sid = m_header.group(1)
            if validate_surface_id(sid):
                current_id = sid
                current_attrs = {}
            continue

        # 3. Indented key: value continuation, only valid in open block.
        m_kv = _KV_RE.match(stripped)
        if m_kv and current_id is not None and had_indent:
            current_attrs[m_kv.group(1)] = m_kv.group(2)
            continue

        # Anything else: ignore.

    _flush()
    return out


def discover_surfaces_yaml(repo_root: Path | None = None) -> Path | None:
    """Locate a usable surfaces.yaml on disk.

    Precedence:
      1. <repo_root>/defense-beacon/reference/surfaces.yaml (if exists)
      2. <repo_root>/defense-beacon/reference/surfaces.example.yaml
      3. ~/projects/DEFENSE-BEACON/surfaces.yaml (operator-managed registry)
      4. ~/projects/DEFENSE-BEACON/surfaces.example.yaml

    Returns None if none of the candidates exist.
    """
    candidates: list[Path] = []
    if repo_root is not None:
        candidates.append(repo_root / "defense-beacon" / "reference" / "surfaces.yaml")
        candidates.append(repo_root / "defense-beacon" / "reference" / "surfaces.example.yaml")
    home_db = Path.home() / "projects" / "DEFENSE-BEACON"
    candidates.append(home_db / "surfaces.yaml")
    candidates.append(home_db / "surfaces.example.yaml")
    for c in candidates:
        if c.is_file():
            return c
    return None


def load_surfaces(path: Path) -> list[SurfaceSpec]:
    """Read + parse a surfaces.yaml file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_surfaces_text(text)


__all__ = [
    "SurfaceSpec",
    "parse_surfaces_text",
    "discover_surfaces_yaml",
    "load_surfaces",
]
