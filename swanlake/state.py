"""State-root resolution and helpers.

Spec sections A3 + A11: ~/.swanlake/ is the unified state root. Operator
override path: --state-root flag (highest precedence) -> SWANLAKE_STATE_ROOT
env var -> default ~/.swanlake/.

The state root is created mode 0700 on first touch. Existing files inside
are never modified by ensure_state_root() -- that function only mkdir-s the
directory and chmod-s the dir itself. v0.1 state (canary-strings.txt,
canary-hits/) lives here untouched (spec R3).
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional


DEFAULT_STATE_ROOT = Path.home() / ".swanlake"

# Module-level mutable so the CLI can override after parsing --state-root.
# Tests patch this directly.
_STATE_ROOT: Path = DEFAULT_STATE_ROOT


def get_state_root() -> Path:
    """Return the currently active state root."""
    return _STATE_ROOT


def set_state_root(path: Path | str) -> None:
    """Override the active state root. Called by the CLI after argparse."""
    global _STATE_ROOT
    _STATE_ROOT = Path(path).expanduser()


def resolve_state_root(cli_override: Optional[str] = None) -> Path:
    """Pick the state root from precedence chain: CLI > env > default.

    Does not touch the filesystem. Pair with `ensure_state_root()` to
    actually create the directory.
    """
    if cli_override:
        return Path(cli_override).expanduser()
    env = os.environ.get("SWANLAKE_STATE_ROOT")
    if env:
        return Path(env).expanduser()
    return DEFAULT_STATE_ROOT


def ensure_state_root(root: Optional[Path] = None) -> Path:
    """Create the state root directory mode 0700 if absent. Never touches
    files already inside the directory (R3 mitigation).

    Returns the resolved path. Idempotent. If the directory already exists
    with looser permissions, this function tightens it back to 0700 so a
    fresh-machine accidental mkdir doesn't leave a 0755 state dir behind.
    """
    target = root if root is not None else get_state_root()
    target = target.expanduser()
    # mkdir is no-op if the path already exists.
    target.mkdir(parents=True, exist_ok=True)
    # Tighten perms to 0700. Only touches the directory itself, not the
    # contents -- existing files (canary-strings.txt etc.) keep their modes.
    try:
        os.chmod(target, stat.S_IRWXU)
    except OSError:
        # On exotic filesystems (some FUSE mounts, /mnt/c if anyone misuses
        # it) chmod silently no-ops. We do not raise; the audit logger will
        # surface unwritable state via its own catch-all.
        pass
    return target


def state_path(name: str) -> Path:
    """Return the absolute path to a state file inside the state root.

    No filesystem side effects. Caller is responsible for ensure_state_root()
    before writing.
    """
    return get_state_root() / name
