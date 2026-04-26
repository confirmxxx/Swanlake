#!/usr/bin/env python3
"""
Defense Beacon canary generator + registry.

Generates 2 canary tokens per surface (one shaped-like-a-secret, one subtle
phrase), registers them in the local canary file so a downstream canary-match
hook picks them up, and emits filled beacon files in ./out/<surface-id>.md
ready to paste into their target surfaces.

Idempotent: canaries for a known surface are reused on re-run. Use --rotate
<surface-id> to force regeneration for one surface.

Surface list is read from surfaces.yaml (plain-text, one id per line;
# comments allowed). If surfaces.yaml does not exist, a minimal example
list is used.

Registry path: $SWANLAKE_REGISTRY, defaulting to ~/.swanlake/canary-strings.txt.

Usage:
    python3 make-canaries.py                 # generate/refresh all surfaces
    python3 make-canaries.py --list          # print surface -> canary mapping
    python3 make-canaries.py --rotate <id>   # force-regenerate one surface
    python3 make-canaries.py --surfaces a,b  # operate on a subset
    python3 make-canaries.py --version       # print script version and exit
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import secrets
import string
import sys
from pathlib import Path

# Script version. Bumped in lockstep with any change to the canary token
# format, registry layout, or beacon emission contract that downstream
# consumers (e.g. swanlake's beacon subprocess wrapper) need to gate on.
# Surfaced via --version so callers can pin a minimum without resorting to
# file-mtime, which `git checkout` bumps without changing content.
__version__ = "1.1.0"

HOME = Path(os.path.expanduser("~"))
REPO_DIR = Path(__file__).resolve().parent
BEACON_TEMPLATE = REPO_DIR / "beacon-template-v1.md"
SURFACES_FILE = REPO_DIR / "surfaces.yaml"
OUT_DIR = REPO_DIR / "out"
STATE_FILE = REPO_DIR / ".canary-state.json"

REGISTRY = Path(os.environ.get(
    "SWANLAKE_REGISTRY",
    str(HOME / ".swanlake" / "canary-strings.txt"),
))

# Surface-id grammar: lowercase alphanumerics + hyphens; first and last char
# alphanumeric; 2–64 chars total. Rejects path-traversal (`..`), embedded
# newlines, shell metacharacters, and anything else that could break the
# registry format or escape the OUT_DIR write.
SURFACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")


def _validate_surface_id(sid: str, context: str) -> None:
    if not SURFACE_ID_RE.match(sid):
        raise SystemExit(
            f"invalid surface-id {sid!r} ({context}). "
            f"Must match [a-z0-9][a-z0-9-]{{0,62}}[a-z0-9]."
        )


# Minimal example surface list used when surfaces.yaml is missing.
# Real deployments: copy surfaces.example.yaml -> surfaces.yaml and edit.
EXAMPLE_SURFACES = [
    "cms-workspace-root",
    "cms-project-alpha",
    "db-project-alpha",
    "deploy-project-alpha",
    "repo-project-alpha",
    "routine-intel-weekly",
    "agent-harness-global",
    "agent-project-alpha",
    "vault-root",
]


def load_surfaces() -> list[str]:
    if not SURFACES_FILE.exists():
        return EXAMPLE_SURFACES
    ids = []
    for raw in SURFACES_FILE.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            _validate_surface_id(line, f"in {SURFACES_FILE.name}")
            ids.append(line)
    return ids or EXAMPLE_SURFACES


# -------- token shapes --------

def _hex(n: int) -> str:
    return secrets.token_hex(n)


def _alnum(n: int) -> str:
    alpha = string.ascii_letters + string.digits
    return "".join(secrets.choice(alpha) for _ in range(n))


# Rotating set of "obvious looking secret" shapes. If an agent is trained
# to exfiltrate anything that looks like a real secret, it grabs these first.
SECRET_SHAPES = [
    lambda sid: f"AKIA_BEACON_{_hex(10).upper()}",
    lambda sid: f"ghp_beacon_{_hex(20)}",
    lambda sid: f"sk_live_beacon_{_hex(12)}",
    lambda sid: f"xoxb-beacon-{_hex(4)}-{_hex(4)}-{_alnum(16)}",
    lambda sid: f"postgres://beacon:{_hex(16)}@beacon-{sid[:20]}.invalid:5432/none",
    lambda sid: f"eyJhbGciOiJIUzI1NiJ9.beacon_{_hex(10)}.{_hex(16)}",
    lambda sid: f"AIzaSyBEACON{_alnum(27)}",
]


def shaped_secret(surface_id: str) -> str:
    # Deterministic shape family per surface — visual triage stays consistent
    # across rotations.
    fn = SECRET_SHAPES[sum(ord(c) for c in surface_id) % len(SECRET_SHAPES)]
    return fn(re.sub(r"[^a-z0-9]", "-", surface_id.lower()))


def subtle_phrase(surface_id: str) -> str:
    uid = _alnum(8)
    slug = re.sub(r"[^a-z0-9]+", "-", surface_id.lower()).strip("-")
    return f"beacon-attrib-{slug}-{uid}"


# -------- state --------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"surfaces": {}, "schema": 1}


def _atomic_write_text(path: Path, content: str) -> None:
    """Write content atomically: write to temp, fsync, rename. Prevents
    corruption under concurrent invocations and partial writes on crash."""
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def save_state(state: dict) -> None:
    _atomic_write_text(STATE_FILE, json.dumps(state, indent=2, sort_keys=True))


# -------- registry --------

REGISTRY_MARK_BEGIN = "# --- defense-beacon canaries (managed) ---"
REGISTRY_MARK_END = "# --- end defense-beacon canaries ---"


def update_registry(state: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    existing = REGISTRY.read_text() if REGISTRY.exists() else ""

    # Strip any old managed block — keep user-authored lines untouched.
    # Anchored to line-start + line-end (MULTILINE) so a literal marker
    # appearing inside a user comment or quoted example does not anchor
    # the strip to the wrong position.
    pattern = re.compile(
        r"^" + re.escape(REGISTRY_MARK_BEGIN) + r"[^\n]*\n"
        r".*?"
        r"^" + re.escape(REGISTRY_MARK_END) + r"[^\n]*\n?",
        re.DOTALL | re.MULTILINE,
    )
    cleaned = pattern.sub("", existing).rstrip() + "\n"

    lines = [REGISTRY_MARK_BEGIN]
    lines.append("# Generated by defense-beacon/reference/make-canaries.py")
    lines.append("# Format: one literal string per line. Do not rename without")
    lines.append("# redeploying the beacon on the affected surface.")
    for sid in sorted(state["surfaces"].keys()):
        entry = state["surfaces"][sid]
        lines.append(f"# surface: {sid} (issued {entry.get('issued_utc', '?')})")
        lines.append(entry["shaped"])
        lines.append(entry["phrase"])
    lines.append(REGISTRY_MARK_END)

    _atomic_write_text(REGISTRY, cleaned + "\n".join(lines) + "\n")


# -------- beacon emission --------

def emit_beacon(surface_id: str, shaped: str, phrase: str, issued_utc: str) -> Path:
    # Defense-in-depth: surface-id was validated by _validate_surface_id at every
    # intake point, but re-check here in case a future caller bypasses those
    # paths. Grammar forbids path separators and `..`, but a symlinked OUT_DIR
    # could still redirect writes outside the repo — so resolve and verify.
    _validate_surface_id(surface_id, context="emit_beacon")
    tpl = BEACON_TEMPLATE.read_text()
    filled = (
        tpl.replace("{{SURFACE_ID}}", surface_id)
        .replace("{{ISSUED_UTC}}", issued_utc)
        .replace("{{CANARY_TOKEN_1}}", shaped)
        .replace("{{CANARY_TOKEN_2}}", phrase)
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{surface_id}.md"
    out_resolved = OUT_DIR.resolve()
    path_resolved = path.resolve()
    try:
        path_resolved.relative_to(out_resolved)
    except ValueError:
        raise SystemExit(
            f"path traversal guard: {surface_id!r} would resolve to "
            f"{path_resolved}, outside OUT_DIR {out_resolved}"
        )
    path.write_text(filled)
    return path


# -------- ops --------

def op_list(state: dict) -> None:
    if not state["surfaces"]:
        print("(no surfaces registered yet — run without --list first)")
        return
    print(f"{'surface':40}  {'issued':20}  shaped / phrase")
    print("-" * 100)
    for sid in sorted(state["surfaces"].keys()):
        e = state["surfaces"][sid]
        print(f"{sid:40}  {e.get('issued_utc', '?'):20}  {e['shaped']}")
        print(f"{'':40}  {'':20}  {e['phrase']}")


def op_generate(state: dict, surfaces: list[str], force_rotate: set[str]) -> None:
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    changed = 0
    for sid in surfaces:
        existing = state["surfaces"].get(sid)
        rotate = sid in force_rotate
        if existing and not rotate:
            shaped = existing["shaped"]
            phrase = existing["phrase"]
            issued = existing["issued_utc"]
        else:
            shaped = shaped_secret(sid)
            phrase = subtle_phrase(sid)
            issued = now
            state["surfaces"][sid] = {
                "shaped": shaped,
                "phrase": phrase,
                "issued_utc": issued,
            }
            changed += 1
        out = emit_beacon(sid, shaped, phrase, issued)
        tag = "NEW" if sid in force_rotate or not existing else "ok "
        print(f"  [{tag}] {sid:40}  -> {out.relative_to(REPO_DIR)}")
    print(f"\n{changed} surface(s) newly generated; {len(surfaces) - changed} reused.")


def _acquire_lock():
    """Advisory file lock around the state read-modify-write sequence.
    Prevents concurrent invocations on the same machine from both reading the
    same state, independently mutating it, and racing on the atomic writes
    (last-writer-wins, missing surfaces from the loser). Lock is released when
    the returned handle is garbage-collected or the process exits."""
    import fcntl
    lock_path = STATE_FILE.with_suffix(STATE_FILE.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(
            f"error: another make-canaries.py is holding {lock_path}; "
            f"wait for it to finish or remove the lock file if stale.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return fh


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Defense beacon canary generator")
    # --version short-circuits before any state read or write so it works
    # even when ~/.swanlake/ is missing or read-only. argparse handles
    # the print + sys.exit(0) on its own when the action fires.
    p.add_argument(
        "--version",
        action="version",
        version=f"make-canaries.py {__version__}",
    )
    p.add_argument("--list", action="store_true", help="Show surface -> canary mapping")
    p.add_argument("--rotate", default="", help="Comma-separated surface ids to rotate")
    p.add_argument("--surfaces", default="", help="Comma-separated subset to operate on")
    args = p.parse_args(argv)

    if not BEACON_TEMPLATE.exists():
        print(f"error: beacon template missing at {BEACON_TEMPLATE}", file=sys.stderr)
        return 2

    all_surfaces = load_surfaces()

    if args.list:
        state = load_state()
        op_list(state)
        return 0

    # Lock covers the state read-modify-write sequence. --list is read-only
    # and does not need the lock.
    _lock = _acquire_lock()  # noqa: F841 — handle kept alive via local ref
    state = load_state()

    subset = [s.strip() for s in args.surfaces.split(",") if s.strip()] or all_surfaces
    for sid in subset:
        _validate_surface_id(sid, context="--surfaces argument")
    unknown = [s for s in subset if s not in all_surfaces]
    if unknown:
        print(f"error: unknown surface ids: {unknown}", file=sys.stderr)
        print(f"known: {all_surfaces}", file=sys.stderr)
        return 2

    force_rotate = {s.strip() for s in args.rotate.split(",") if s.strip()}
    for sid in force_rotate:
        _validate_surface_id(sid, context="--rotate argument")
    unknown_rot = [s for s in force_rotate if s not in all_surfaces]
    if unknown_rot:
        print(f"error: unknown rotate ids: {unknown_rot}", file=sys.stderr)
        return 2

    op_generate(state, subset, force_rotate)
    save_state(state)
    update_registry(state)
    print(f"\nregistry: {REGISTRY}")
    print(f"output:   {OUT_DIR}")
    print(f"state:    {STATE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
