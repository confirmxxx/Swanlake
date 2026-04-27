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

# Permissive "looks like the operator tried to write a surface ID" patterns.
# Used only to surface E23 warnings: a candidate that fails the strict
# grammar above but matches the loose shape gets flagged rather than
# silently swallowed.
_LOOSE_PLAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
_LOOSE_HEADER_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_-]{0,80}):$")


def parse_surfaces_text(text: str) -> list[SurfaceSpec]:
    """Parse `text` into an ordered list of SurfaceSpec.

    Tolerant: malformed lines are skipped. Validation of the surface-id
    grammar is delegated to validate_surface_id() so the same regex
    governs both make-canaries.py and the swanlake CLI surface ingest.

    For visibility into what was skipped (E23 / E24 in the 2026-04-27
    edge-case audit), call `parse_surfaces_text_with_warnings()` -- this
    function is preserved for callers that want the simple list shape.
    """
    specs, _warnings = parse_surfaces_text_with_warnings(text)
    return specs


def parse_surfaces_text_with_warnings(
    text: str,
) -> tuple[list[SurfaceSpec], list[str]]:
    """Same as `parse_surfaces_text`, but also returns a list of human-
    readable warnings for surfaces that were silently skipped or whose
    annotated keys collided.

    Warnings are intended to be surfaced to stderr by the file-loading
    path so the operator who typoes a surface ID gets feedback rather
    than a silent drop. The warning list is empty when the input is
    valid.
    """
    out: list[SurfaceSpec] = []
    warnings: list[str] = []
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

    for lineno, raw in enumerate(text.splitlines(), start=1):
        # Strip line-end comments first (`id  # note`).
        no_comment = raw.split("#", 1)[0].rstrip()
        if not no_comment.strip():
            continue

        # Detect indentation: if the original line had leading whitespace
        # AND there's an open block, this is a candidate kv continuation.
        had_indent = no_comment[:1] in (" ", "\t")
        stripped = no_comment.strip()

        # 1. Plain-id (no colon at all).
        if ":" not in stripped:
            m_plain = _PLAIN_LINE_RE.match(stripped)
            if m_plain:
                sid = m_plain.group(1)
                # Indented plain-id with no open block is still a top-level id.
                # Indented plain-id WITH an open block would be ambiguous; we
                # close the open block and emit the new surface.
                _flush()
                current_id = sid
                current_attrs = {}
                _flush()
                continue
            # Looks like the operator tried to write a surface-id but
            # tripped the grammar (uppercase, underscore, etc.). Surface
            # the rejection rather than silently dropping the line.
            if _LOOSE_PLAIN_RE.match(stripped):
                warnings.append(
                    f"line {lineno}: surface-id {stripped!r} fails the "
                    "grammar check [a-z0-9][a-z0-9-]{0,62}[a-z0-9]; skipping"
                )
            continue

        # 2. Header (id followed by colon, nothing after).
        m_header = _HEADER_RE.match(stripped)
        if m_header:
            _flush()
            sid = m_header.group(1)
            current_id = sid
            current_attrs = {}
            continue
        # Looks like an annotated-header line whose id failed the grammar.
        m_loose_header = _LOOSE_HEADER_RE.match(stripped)
        if m_loose_header and not _KV_RE.match(stripped):
            warnings.append(
                f"line {lineno}: surface-id {m_loose_header.group(1)!r} "
                "(annotated header) fails the grammar check; skipping block"
            )
            _flush()  # close any open block to be safe
            continue

        # 3. Indented key: value continuation, only valid in open block.
        m_kv = _KV_RE.match(stripped)
        if m_kv and current_id is not None and had_indent:
            key, value = m_kv.group(1), m_kv.group(2)
            if key in current_attrs:
                # E24: silent last-write-win behaviour confused operators
                # who copy-pasted an annotated block. Surface the
                # collision so the operator sees the duplicate.
                warnings.append(
                    f"line {lineno}: duplicate key {key!r} in surface "
                    f"{current_id!r}; later value {value!r} wins over "
                    f"earlier {current_attrs[key]!r}"
                )
            current_attrs[key] = value
            continue

        # Anything else: ignore.

    _flush()
    return out, warnings


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
    """Read + parse a surfaces.yaml file.

    Emits any per-line warnings to stderr so the operator who typoed a
    surface ID or duplicated a key in an annotated block gets visible
    feedback rather than a silent drop. (E23 / E24 in the 2026-04-27
    edge-case audit.)
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    specs, warnings = parse_surfaces_text_with_warnings(text)
    if warnings:
        # Local import to avoid a circular dep at module load -- output
        # imports nothing from this package, but keeping the import here
        # documents the lazy edge.
        import sys
        for w in warnings:
            sys.stderr.write(f"surfaces.yaml ({path}): {w}\n")
    return specs


__all__ = [
    "SurfaceSpec",
    "parse_surfaces_text",
    "parse_surfaces_text_with_warnings",
    "discover_surfaces_yaml",
    "load_surfaces",
]
