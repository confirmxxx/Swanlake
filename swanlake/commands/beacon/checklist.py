"""`swanlake beacon checklist` -- emit paste-checklist for REMOTE surfaces.

`--remind-export-stale <duration>` checks the mtime of
~/.swanlake/routines-export.json and warns on stderr if it's older than
the given duration. D8 says routines are export-only; the operator
re-exports on a cadence they pick, and this flag surfaces drift in that
cadence.



Spec §6. Default output: stdout. `--out FILE` writes mode 0600 with a
stderr warning that the file contains live canary tokens.

The checklist is a markdown document with one fenced block per pending
REMOTE surface. Each block carries:
  - target identifier (page URL / env var key + project ref / repo+path / routine id)
  - paste action (one-line imperative tailored to the type)
  - the literal beacon block from make-canaries.py output
  - the verify command: `swanlake beacon verify --surface <id>`

Per spec N9 + R6: the checklist file is the only place canary literals
appear in human-facing output, because the operator MUST paste them.
The disposal warning + 0600 mode mitigate the live-document risk.

Per N1 / D4: stdout default minimizes on-disk live-canary registries.

Subprocess to make-canaries.py per D6 / D9, gated on --version >= 1.1.0
(R5). The script's `out/<surface>.md` file carries the operator-facing
beacon body to paste.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from swanlake import state as _state
from swanlake.commands.beacon import _history, _surfaces
from swanlake.commands.beacon._registry import (
    SCOPE_REMOTE,
    SURFACE_TYPES,
    get_type,
    infer_type,
)
from swanlake.exit_codes import CLEAN, NOT_IMPLEMENTED, USAGE
from swanlake.output import eprint, print_line


# Per-type "paste action" template. The {target} placeholder is filled
# from the surfaces.yaml `target:` annotation; if missing, we render
# `<set target in surfaces.yaml>` as a placeholder.
_PASTE_ACTION_TEMPLATES = {
    "notion": (
        "open the workspace page at `{target}` and replace any existing "
        "DEFENSE BEACON v1 block with the content below; preserve all "
        "other page content"
    ),
    "supabase-env": (
        "set the env var `{target}` (in the `<project-ref>` you configured) "
        "to the literal value below; do NOT echo this to logs"
    ),
    "vercel-env": (
        "set the env var `{target}` (in the matching Vercel project) to the "
        "literal value below; do NOT echo this to logs"
    ),
    "github-public": (
        "open a PR against `{target}` on a branch named "
        "`beacon-deploy-<UTC-date>` adding the block below; the PR must be "
        "reviewed by a human before merge"
    ),
    "claude-routine": (
        "edit routine `{target}` via the routines UI; replace the existing "
        "beacon block (if any) with the content below"
    ),
}


_DURATION_RE = re.compile(r"^(\d+)([dhm])$")


def _parse_duration_seconds(spec: str) -> int | None:
    """Parse `30d` / `12h` / `15m` into seconds. Returns None on bad input."""
    m = _DURATION_RE.match(spec.strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        return n * 86400
    if unit == "h":
        return n * 3600
    if unit == "m":
        return n * 60
    return None


def _check_routines_export_stale(spec: str) -> str | None:
    """Return a warning string if the export file is older than `spec`.

    Returns None if the file is fresh, the spec is malformed (caller's
    bug; we surface a different warning), or the file is absent (a
    distinct warning -- the operator hasn't exported yet).
    """
    duration = _parse_duration_seconds(spec)
    if duration is None:
        return f"--remind-export-stale: bad duration {spec!r} (use e.g. 30d, 12h, 15m)"
    export_path = _state.state_path("routines-export.json")
    if not export_path.exists():
        return (
            f"routines export not found at {export_path}; "
            "run a manual export from the routines UI"
        )
    try:
        mtime = export_path.stat().st_mtime
    except OSError:
        return f"could not stat {export_path}"
    age = time.time() - mtime
    if age > duration:
        days = int(age / 86400)
        return (
            f"routines export at {export_path} is {days}d old "
            f"(threshold {spec}); re-export from the routines UI"
        )
    return None


def _resolve_repo_root() -> Path | None:
    try:
        from swanlake import _compat
        return _compat.find_repo_root()
    except Exception:
        return None


def _generate_block(repo_root: Path, surface: str) -> tuple[str | None, str | None]:
    """Subprocess to make-canaries.py and read out/<surface>.md.

    Returns (content, error). Identical contract to _local._step7_generate_beacon
    but kept inline here so the checklist module doesn't depend on the
    deploy module's internals.
    """
    script = repo_root / "defense-beacon" / "reference" / "make-canaries.py"
    if not script.is_file():
        return None, f"make-canaries.py not at {script}"

    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--version"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"make-canaries.py --version failed: {type(e).__name__}"
    if proc.returncode != 0:
        return None, f"make-canaries.py --version exit {proc.returncode}"
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", proc.stdout or proc.stderr)
    if not m:
        return None, "could not parse make-canaries.py version"
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if (major, minor, patch) < (1, 1, 0):
        return None, (
            f"make-canaries.py is version {major}.{minor}.{patch}; need >= 1.1.0"
        )

    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--surfaces", surface],
            capture_output=True, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"make-canaries.py --surfaces failed: {type(e).__name__}"
    if proc.returncode != 0:
        return None, f"make-canaries.py exit {proc.returncode}: {proc.stderr.strip()}"

    out_path = repo_root / "defense-beacon" / "reference" / "out" / f"{surface}.md"
    if not out_path.is_file():
        return None, f"expected {out_path}; not found"
    try:
        return out_path.read_text(encoding="utf-8"), None
    except OSError as e:
        return None, f"could not read {out_path}: {e}"


def _paste_action(type_id: str, target: str | None) -> str:
    template = _PASTE_ACTION_TEMPLATES.get(type_id)
    if template is None:
        return f"paste the block below into the surface (type={type_id})"
    rendered_target = target or "<set target in surfaces.yaml>"
    return template.format(target=rendered_target)


def _block_for_surface(
    surface: str,
    type_id: str,
    target: str | None,
    repo_root: Path,
) -> tuple[str, str | None]:
    """Render one fenced block. Returns (markdown, error_or_none)."""
    content, err = _generate_block(repo_root, surface)
    if err:
        # Render an inline error block so the operator sees the failure
        # in the checklist itself rather than just stderr.
        block = (
            f"## {surface}\n\n"
            f"- target: {target or '(unset)'}\n"
            f"- type: {type_id}\n"
            f"- ERROR: {err}\n"
        )
        return block, err

    paste_action = _paste_action(type_id, target)
    block = (
        f"## {surface}\n\n"
        f"- target: {target or '(unset)'}\n"
        f"- surface-id: {surface}\n"
        f"- type: {type_id}\n"
        f"- paste action: {paste_action}\n"
        f"- verify after: `swanlake beacon verify --surface {surface}`\n\n"
        f"```markdown\n"
        f"{content.rstrip()}\n"
        f"```\n"
    )
    return block, None


def _build_checklist(
    surfaces: list[tuple[str, str, str | None]],
    repo_root: Path,
) -> tuple[str, int]:
    """Build the full checklist markdown. Returns (text, n_errors)."""
    header = (
        "# Swanlake REMOTE-surface deploy checklist\n\n"
        "**DO NOT COMMIT** -- this file contains live canary tokens.\n\n"
        "Each fenced block below corresponds to one REMOTE surface that "
        "needs a manual paste. After pasting, run the listed verify "
        "command before moving to the next surface.\n\n"
        "Suggested disposal: `shred -u <this-file>` after every block has "
        "been pasted and verified, OR move to "
        "`~/.swanlake/beacon-backups/checklists/` and `rm` after audit.\n\n"
        "---\n\n"
    )
    blocks: list[str] = []
    n_errors = 0
    for surface, type_id, target in surfaces:
        block, err = _block_for_surface(surface, type_id, target, repo_root)
        if err:
            n_errors += 1
        blocks.append(block)
    return header + "\n".join(blocks), n_errors


def _collect_remote_surfaces(only_surface: str | None) -> list[tuple[str, str, str | None]]:
    """Read surfaces.yaml; return (surface, type, target) for each REMOTE entry.

    If `only_surface` is set, restrict to that one surface (regardless of
    its type -- the caller is taking responsibility).
    """
    repo_root = _resolve_repo_root()
    yaml_path = _surfaces.discover_surfaces_yaml(repo_root)
    if yaml_path is None:
        return []
    try:
        specs = _surfaces.load_surfaces(yaml_path)
    except OSError:
        return []
    out: list[tuple[str, str, str | None]] = []
    for spec in specs:
        if only_surface is not None and spec.surface_id != only_surface:
            continue
        type_obj = get_type(spec.type_id)
        if type_obj is None:
            continue
        if only_surface is None and not type_obj.is_remote:
            continue
        out.append((spec.surface_id, spec.type_id, spec.target))
    return out


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    out_path_str: str | None = getattr(args, "out", None)
    only_surface = getattr(args, "surface", None)
    remind_stale: str | None = getattr(args, "remind_export_stale", None)

    # Routines-export staleness warning. Goes to stderr; never blocks.
    if remind_stale:
        warning = _check_routines_export_stale(remind_stale)
        if warning:
            eprint(f"swanlake beacon checklist: {warning}")

    repo_root = _resolve_repo_root()
    if repo_root is None:
        eprint(
            "swanlake beacon checklist: cannot locate Swanlake repo root "
            "(set SWANLAKE_REPO_ROOT or run from inside a Swanlake clone)"
        )
        return USAGE

    surfaces = _collect_remote_surfaces(only_surface)
    if not surfaces:
        if only_surface:
            eprint(
                f"swanlake beacon checklist: surface {only_surface!r} not in "
                "surfaces.yaml (or has no entry to render)"
            )
        else:
            eprint(
                "swanlake beacon checklist: no REMOTE surfaces in surfaces.yaml"
            )
        return USAGE

    text, n_errors = _build_checklist(surfaces, repo_root)

    # Write to FILE or stdout.
    if out_path_str:
        out_path = Path(out_path_str).expanduser()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # Write mode 0600 -- per D4 / R6.
            fd = os.open(
                str(out_path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                fp.write(text)
        except OSError as e:
            eprint(f"swanlake beacon checklist: cannot write {out_path}: {e}")
            return USAGE
        # Ensure mode is 0600 even if umask widened it.
        try:
            os.chmod(out_path, 0o600)
        except OSError:
            pass
        eprint(
            f"checklist written to {out_path} (mode 0600). "
            "WARNING: contains live canary tokens; delete after pasting "
            "(suggested: shred -u or move to ~/.swanlake/beacon-backups/checklists/)"
        )
    else:
        if not quiet:
            sys.stdout.write(text)

    # History row.
    try:
        _history.append({
            "op": "checklist",
            "surface": only_surface,
            "type": None,
            "method": "remote-checklist",
            "outcome": (
                "checklist-printed" if not out_path_str else "checklist-written"
            ),
            "summary": {
                "n_surfaces": len(surfaces),
                "n_errors": n_errors,
                "out_path": out_path_str,
            },
        })
    except Exception:
        pass

    return CLEAN


__all__ = ["run"]
