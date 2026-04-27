"""Install marker — record where Swanlake was installed from.

Spec: docs/v0.3.x-worktree-install-isolation-spec.md (T1 + T2).

Background. `pip install -e .` writes a single per-environment editable
`.pth` finder pointing at one absolute source path. If a second
`pip install -e .` runs from a different worktree (typical for
background build agents in `.claude/worktrees/<branch>/`), the global
pointer silently flips. The next `swanlake --version` from any shell on
the machine reports whatever __version__ the new source declared.

This module is the in-CLI defense:

  - `write_marker(source_path)` is called by the setuptools cmdclass
    hooks in setup.py during `pip install` / `pip install -e .`.
    It records the source path (and the interpreter that did the
    install) into ~/.swanlake/.install-marker.

  - `read_marker()` parses the marker back into a typed dict, or
    returns None if the file is missing or malformed. Never raises.

  - `check_drift()` compares the marker's source_path against the
    currently-imported swanlake package directory and returns a
    typed dict describing the result. Never raises.

  - `format_drift_warning()` renders the drift dict into the multi-line
    stderr warning consumed by `swanlake/cli.py`.

The marker format is two/three `key=value` lines, no JSON/TOML
ceremony. Fields:

    source_path=<absolute path of the source dir setup.py was invoked from>
    installed_at=<UTC ISO-8601 timestamp>
    python_executable=<sys.executable at install time>

The third field disambiguates multi-interpreter installs (Python 3.11
and 3.12 on the same host with separate site-packages but a shared
~/.swanlake/) — a marker written by 3.11 should not warn a 3.12 CLI
that ran a different `pip install` against its own site-packages.
"""
from __future__ import annotations

import datetime as _dt
import os
import stat
import sys
from pathlib import Path
from typing import Any, Optional


MARKER_FILENAME = ".install-marker"
DRIFT_WARN_ENV = "SWANLAKE_NO_INSTALL_DRIFT_WARN"

# Prefixes pip uses for the transient build/extract dirs it creates while
# installing from a tarball / VCS / sdist. The setuptools cmdclass hook
# fires from that transient dir (because that's where setup.py lives at
# install time), so the marker captures a path that pip will delete the
# instant the install finishes. Recognising these prefixes lets the
# drift check distinguish "marker points at a long-gone build dir
# (false positive)" from "marker points at a stale-but-real source root
# in another worktree (true drift)". List sourced from pip 21.x-26.x
# `pip._internal.utils.temp_dir.TempDirectory` kinds (`req-build`,
# `build`, `install`).
_PIP_TRANSIENT_BUILD_PREFIXES = (
    "/tmp/pip-req-build-",
    "/tmp/pip-build-",
    "/tmp/pip-install-",
)


def _state_root() -> Path:
    """Resolve the state root the same way `swanlake.state` does.

    Duplicated here (rather than imported) so the setuptools cmdclass
    hook can write the marker before the package is even installed
    (importing swanlake from setup.py would be circular). The two-line
    duplication is intentional — keep the install path zero-import.
    """
    env = os.environ.get("SWANLAKE_STATE_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".swanlake"


def write_marker(source_path: str | Path, *, state_root: Optional[Path] = None) -> Optional[Path]:
    """Write `~/.swanlake/.install-marker` mode 0600 with the install record.

    Returns the marker path on success, or None if the write failed for
    any reason (unwritable home, read-only filesystem, etc.). The
    install must never fail because the marker write failed — pip
    rolls back too aggressively, and a missing marker degrades cleanly
    (the CLI startup check treats missing markers as "no drift to
    detect").

    Called from `setup.py` cmdclass hooks. Also exposed for the
    `swanlake doctor --repair install-marker` flow planned for v0.4.
    """
    target_root = state_root if state_root is not None else _state_root()
    try:
        target_root.mkdir(parents=True, exist_ok=True)
        # Tighten the dir to 0700 if we just created it. Match the
        # `swanlake.state.ensure_state_root` contract.
        try:
            os.chmod(target_root, stat.S_IRWXU)
        except OSError:
            # Some FUSE/exotic filesystems silently no-op chmod.
            # Not fatal — the marker is informational, not a credential.
            pass

        marker = target_root / MARKER_FILENAME
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        body = (
            f"source_path={Path(source_path).resolve()}\n"
            f"installed_at={ts}\n"
            f"python_executable={sys.executable}\n"
        )
        # Atomic write via tempfile + os.replace. Avoids a half-written
        # marker if the write is interrupted mid-flush.
        tmp = marker.with_suffix(marker.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fp:
            fp.write(body)
        os.replace(tmp, marker)
        try:
            os.chmod(marker, 0o600)
        except OSError:
            pass
        return marker
    except OSError:
        # Surface a single stderr line so the operator sees what
        # happened, then degrade. Never raise — see docstring.
        try:
            sys.stderr.write(
                "swanlake: could not write install marker "
                f"under {target_root} (continuing install).\n"
            )
        except OSError:
            pass
        return None


def read_marker(*, state_root: Optional[Path] = None) -> Optional[dict[str, str]]:
    """Parse the marker file. Returns None if missing/malformed.

    The parser tolerates trailing whitespace, blank lines, and
    `# comment` lines. Unknown keys are preserved in the returned dict
    so future fields don't break older readers.
    """
    target_root = state_root if state_root is not None else _state_root()
    marker = target_root / MARKER_FILENAME
    if not marker.is_file():
        return None
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError:
        return None
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    if not out:
        return None
    return out


def _is_transient_build_path(source_path: str) -> bool:
    """Return True when `source_path` looks like a pip transient build dir.

    Two signals count as transient (either is sufficient):

      1. The string starts with one of the documented pip prefixes
         (`/tmp/pip-req-build-`, `/tmp/pip-build-`, `/tmp/pip-install-`).
         This catches the common case even after the dir has been
         garbage-collected (the path string is still recognisable).
      2. The path no longer exists on disk. A stale tarball-install
         marker satisfies this once pip wipes the build dir, regardless
         of prefix shape; it is the single most reliable signal that
         the marker can never legitimately match a runtime source.

    The check is a pure function of the marker string + filesystem
    state — never raises, never imports anything heavy. Callers
    (currently `check_drift`) treat a transient-shaped marker the same
    as a missing marker AND opportunistically rewrite it to point at
    the runtime source on first CLI invocation.
    """
    if not source_path:
        return False
    if any(source_path.startswith(prefix) for prefix in _PIP_TRANSIENT_BUILD_PREFIXES):
        return True
    try:
        if not Path(source_path).exists():
            return True
    except OSError:
        # Filesystem error during stat — treat as "can't confirm it
        # exists, so don't fire drift on it". Conservative; the worst
        # case is we self-heal a marker that pointed at a transiently
        # unreachable network mount, which is a benign no-op the next
        # time the operator reruns the CLI from the right place.
        return True
    return False


def _runtime_source_dir() -> Path:
    """Resolve the directory of the currently-imported `swanlake` package.

    `Path(swanlake.__file__).parent` points at the package dir
    (`<source>/swanlake/`); the editable install's source root is one
    level up. Use that as the comparison key for marker drift — the
    marker stores the source root (the dir containing pyproject.toml).
    """
    import swanlake
    pkg_dir = Path(swanlake.__file__).resolve().parent
    return pkg_dir.parent


def check_drift(*, state_root: Optional[Path] = None) -> dict[str, Any]:
    """Compare the runtime source dir against the marker. Never raises.

    Returns a dict with keys:
        status:        "ok" | "no-marker" | "drift" | "cross-interpreter"
        runtime_path:  absolute path of the running swanlake source root
        marker_path:   absolute path stored in the marker (None if no marker)
        marker_python: sys.executable from the marker (None if no marker / older)
        runtime_python: current sys.executable
    """
    runtime = _runtime_source_dir()
    marker = read_marker(state_root=state_root)
    if marker is None:
        return {
            "status": "no-marker",
            "runtime_path": str(runtime),
            "marker_path": None,
            "marker_python": None,
            "runtime_python": sys.executable,
        }
    marker_source = marker.get("source_path")
    marker_python = marker.get("python_executable")
    if not marker_source:
        return {
            "status": "no-marker",
            "runtime_path": str(runtime),
            "marker_path": None,
            "marker_python": marker_python,
            "runtime_python": sys.executable,
        }
    # If the marker was written by a different interpreter than the one
    # running now, suppress the drift warning — the operator likely has
    # multi-interpreter installs (3.11 + 3.12) sharing ~/.swanlake/.
    if marker_python and marker_python != sys.executable:
        return {
            "status": "cross-interpreter",
            "runtime_path": str(runtime),
            "marker_path": marker_source,
            "marker_python": marker_python,
            "runtime_python": sys.executable,
        }
    # Tarball/sdist installs run the cmdclass hook from a transient
    # `/tmp/pip-req-build-<random>/` dir that pip deletes the moment
    # the install finishes. The marker captured that dir; the runtime
    # is wherever pip put the wheel (site-packages). Without this branch
    # every subsequent CLI invocation fires a false-positive drift
    # warning. Treat the transient marker as "first-run, marker not yet
    # established" and opportunistically rewrite it to point at the
    # runtime location so subsequent calls take the fast `ok` path.
    if _is_transient_build_path(marker_source):
        try:
            write_marker(runtime, state_root=state_root)
        except Exception:  # noqa: BLE001 — never crash CLI on warning path
            # write_marker already swallows OSError; this catch covers
            # anything more exotic. The check still degrades to
            # "no-marker" (silent) regardless.
            pass
        return {
            "status": "no-marker",
            "runtime_path": str(runtime),
            "marker_path": None,
            "marker_python": marker_python,
            "runtime_python": sys.executable,
        }
    # Resolve both sides before comparing — avoid false-positive on
    # symlinks (~/.local vs /home/<user>/.local).
    try:
        runtime_resolved = runtime.resolve()
        marker_resolved = Path(marker_source).resolve()
    except OSError:
        # Resolution failure (e.g. marker points at a deleted dir).
        # Treat as drift — that's almost certainly what happened.
        return {
            "status": "drift",
            "runtime_path": str(runtime),
            "marker_path": marker_source,
            "marker_python": marker_python,
            "runtime_python": sys.executable,
        }
    if runtime_resolved == marker_resolved:
        return {
            "status": "ok",
            "runtime_path": str(runtime_resolved),
            "marker_path": str(marker_resolved),
            "marker_python": marker_python,
            "runtime_python": sys.executable,
        }
    return {
        "status": "drift",
        "runtime_path": str(runtime_resolved),
        "marker_path": str(marker_resolved),
        "marker_python": marker_python,
        "runtime_python": sys.executable,
    }


def format_drift_warning(drift: dict[str, Any]) -> str:
    """Render a drift dict into the human-readable stderr warning.

    Returns the warning text including a trailing newline. Caller
    decides whether to actually emit it (quiet flag, env override).
    """
    return (
        "warning: swanlake CLI is running from "
        f"{drift['runtime_path']}\n"
        "         but the install marker at ~/.swanlake/.install-marker points at "
        f"{drift['marker_path']}.\n"
        "         This usually means a background agent ran "
        "`pip install -e .` inside its own worktree.\n"
        "         Run `pip install --force-reinstall "
        f"{drift['runtime_path']}` to reset, or use pipx for isolation.\n"
    )


__all__ = [
    "MARKER_FILENAME",
    "DRIFT_WARN_ENV",
    "write_marker",
    "read_marker",
    "check_drift",
    "format_drift_warning",
]
