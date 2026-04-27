"""Scaffold module -- create a fresh Swanlake-aware project skeleton.

Spec: docs/v0.4-enforcement-spec.md E7.

Layout per --type:

  cc:
    {target}/CLAUDE.md                       <- rendered from template
    {target}/canon/operating-rules.md        <- copied from canon
    {target}/.swanlake-no-beacon.example     <- example opt-out marker

  cma:
    {target}/CLAUDE.md                       <- rendered from template
    {target}/canon/operating-rules.md        <- copied from canon
    {target}/cmas/.gitkeep                   <- placeholder so the dir
                                                survives empty checkouts
    {target}/zones.example.yaml              <- example trust-zone config
    {target}/.swanlake-no-beacon.example     <- example opt-out marker

Refusals:
  - Target dir non-empty without --force -> exit 2
  - Target opted-out via .swanlake-no-beacon at or above -> exit 2
  - --type missing -> exit 2 (argparse catches this earlier)
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from swanlake.commands.beacon import _optout
from swanlake.exit_codes import CLEAN, USAGE
from swanlake.output import eprint, print_json, print_line


VALID_TYPES = ("cc", "cma")


def _templates_root() -> Path:
    """Resolve the bundled init-templates root."""
    return (
        Path(__file__).resolve().parents[2]
        / "adapters"
        / "templates"
        / "init"
    )


def _is_dir_effectively_empty(path: Path) -> bool:
    """True iff the dir contains nothing or only dot-prefixed
    non-VCS entries.

    Any file/dir at the top level (other than `.git` which is allowed
    -- the operator may have run `git init` first) makes the dir
    non-empty for our purposes.
    """
    if not path.exists():
        return True
    if not path.is_dir():
        return False
    for child in path.iterdir():
        # Allow the operator to have run `git init` first; .git is fine.
        if child.name == ".git":
            continue
        return False
    return True


def _render_template(src: Path, project_name: str) -> str:
    """Read `src` and substitute {project_name} placeholders.

    Uses str.format-style braces; non-template placeholders in the
    file (e.g. regex character classes) are pre-escaped in the
    template files via doubled braces.
    """
    text = src.read_text(encoding="utf-8")
    return text.format(project_name=project_name)


def scaffold(
    target: Path,
    *,
    project_type: str,
    project_name: str | None = None,
    force: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Build the project skeleton at `target`. Return (exit_code, payload).

    Read by both the CLI handler (for table/JSON output) and tests
    (for structured assertions).
    """
    target = target.expanduser()
    project_type = project_type.lower()
    if project_type not in VALID_TYPES:
        return USAGE, {
            "error": f"unknown --type: {project_type!r}; expected one of {VALID_TYPES}",
            "target": str(target),
        }

    # Opt-out check -- refuse if any ancestor carries the marker.
    excluded, marker = _optout.is_excluded(
        target=target,
        surface_id="*",
    )
    if excluded:
        return USAGE, {
            "error": (
                f"target {target} is opted out via {marker.path}; "
                "remove the marker before initialising"
            ),
            "target": str(target),
        }

    # Empty-dir gate.
    if target.exists() and not _is_dir_effectively_empty(target) and not force:
        # Count files for the error message.
        try:
            n = sum(1 for _ in target.iterdir())
        except OSError:
            n = -1
        return USAGE, {
            "error": (
                f"target {target} is non-empty ({n} entries); pass --force "
                f"to overwrite, or pick an empty dir"
            ),
            "target": str(target),
        }

    if project_name is None:
        project_name = target.name or "project"

    target.mkdir(parents=True, exist_ok=True)

    # Locate the template tree for this type.
    type_root = _templates_root() / project_type
    if not type_root.is_dir():
        return USAGE, {
            "error": f"bundled template tree missing at {type_root}",
            "target": str(target),
        }

    created: list[str] = []
    skipped: list[dict[str, str]] = []

    # Render CLAUDE.md.
    claude_template = type_root / "CLAUDE.md.template"
    if claude_template.is_file():
        rendered = _render_template(claude_template, project_name)
        out_path = target / "CLAUDE.md"
        if out_path.exists() and not force:
            skipped.append({"path": str(out_path), "reason": "exists"})
        else:
            out_path.write_text(rendered, encoding="utf-8")
            created.append(str(out_path))

    # Copy canon/operating-rules.md.
    canon_src = type_root / "canon" / "operating-rules.md"
    if canon_src.is_file():
        canon_dst_dir = target / "canon"
        canon_dst_dir.mkdir(exist_ok=True)
        canon_dst = canon_dst_dir / "operating-rules.md"
        if canon_dst.exists() and not force:
            skipped.append({"path": str(canon_dst), "reason": "exists"})
        else:
            shutil.copy2(canon_src, canon_dst)
            created.append(str(canon_dst))

    # Drop .swanlake-no-beacon.example.
    example_src = type_root / ".swanlake-no-beacon.example"
    # The cma type doesn't bundle its own copy -- fall back to cc's.
    if not example_src.is_file():
        example_src = _templates_root() / "cc" / ".swanlake-no-beacon.example"
    if example_src.is_file():
        example_dst = target / ".swanlake-no-beacon.example"
        if example_dst.exists() and not force:
            skipped.append({"path": str(example_dst), "reason": "exists"})
        else:
            shutil.copy2(example_src, example_dst)
            created.append(str(example_dst))

    # CMA-specific: cmas/ dir + zones.example.yaml.
    if project_type == "cma":
        cmas_dir = target / "cmas"
        cmas_dir.mkdir(exist_ok=True)
        gitkeep = cmas_dir / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("")
            created.append(str(gitkeep))

        zones_src = type_root / "zones.example.yaml"
        if zones_src.is_file():
            zones_dst = target / "zones.example.yaml"
            if zones_dst.exists() and not force:
                skipped.append({"path": str(zones_dst), "reason": "exists"})
            else:
                shutil.copy2(zones_src, zones_dst)
                created.append(str(zones_dst))

    payload = {
        "target": str(target),
        "type": project_type,
        "project_name": project_name,
        "created": created,
        "skipped": skipped,
    }
    return CLEAN, payload


def run(args) -> int:
    """CLI handler for `swanlake init project`."""
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))
    project_type = getattr(args, "type", None)
    target_str = getattr(args, "target", None) or "."
    force = bool(getattr(args, "force", False))
    project_name = getattr(args, "name", None)

    if not project_type:
        eprint(
            "swanlake init project: --type is required (cc or cma)."
        )
        return USAGE

    target = Path(target_str).resolve()
    rc, payload = scaffold(
        target,
        project_type=project_type,
        project_name=project_name,
        force=force,
    )

    if rc != CLEAN:
        # Error payload -- error message goes to stderr, no JSON
        # spam on the stdout channel.
        eprint(f"swanlake init project: {payload.get('error', 'unknown error')}")
        if json_out:
            print_json(payload, quiet=quiet)
        return rc

    if json_out:
        print_json(payload, quiet=quiet)
    else:
        for path in payload.get("created", []):
            print_line(f"created: {path}", quiet=quiet)
        for entry in payload.get("skipped", []):
            print_line(
                f"skipped: {entry.get('path')} ({entry.get('reason')})",
                quiet=quiet,
            )
        if not quiet:
            if project_type == "cma":
                print_line(
                    "next: drop CMA definitions under cmas/ then run "
                    "'swanlake adapt cma --project .'",
                    quiet=False,
                )
            else:
                print_line(
                    "next: review CLAUDE.md, then commit. Run 'swanlake "
                    "beacon deploy <surface-id>' once you have a surface "
                    "registered.",
                    quiet=False,
                )
    return rc


__all__ = ["scaffold", "run", "VALID_TYPES"]
