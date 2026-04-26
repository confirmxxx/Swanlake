"""Compatibility shims for importing the existing hyphenated tools.

`tools/status-segment.py` and `tools/loop-closure-metric.py` cannot be
imported with a normal `import` statement because Python identifiers
disallow hyphens. They keep the hyphenated names because
~/.claude/hooks/status-line.sh references them by absolute path; renaming
in v0.2 would force a hook rewrite and add scope (see spec A4).

This module loads them once via importlib.util.spec_from_file_location and
re-exports the symbols `swanlake.commands.status` needs. Repo-root
discovery precedence:

    1. SWANLAKE_REPO_ROOT env var (explicit override)
    2. swanlake_repo_path field in ~/.swanlake/config.toml (operator config)
    3. Walk up from this file's location looking for the marker
       `tools/status-segment.py` (works for editable install + dev tree)

Failure to resolve the repo root is a real problem -- the status command
cannot work without the underlying scripts. Surface the failure as a
clear ImportError at first use so the operator gets a useful message
rather than an opaque NoneType crash deep inside _dim_canary.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Optional


REPO_MARKER = Path("tools") / "status-segment.py"

# Cached module objects (lazy load on first access).
_status_segment_mod: Optional[ModuleType] = None
_loop_closure_mod: Optional[ModuleType] = None
_repo_root: Optional[Path] = None


class CompatError(RuntimeError):
    """Raised when the underlying tools cannot be located or loaded."""


def _read_config_repo_path() -> Optional[Path]:
    """Read swanlake_repo_path from ~/.swanlake/config.toml if present."""
    cfg = Path.home() / ".swanlake" / "config.toml"
    if not cfg.exists():
        return None
    try:
        with open(cfg, "rb") as fp:
            data = tomllib.load(fp)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    raw = data.get("swanlake_repo_path")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return None


def _walk_up_for_marker(start: Path) -> Optional[Path]:
    """Walk up from `start` until we find a directory containing REPO_MARKER."""
    cur = start.resolve()
    # Bound the walk -- 16 levels is plenty for any realistic install layout.
    for _ in range(16):
        if (cur / REPO_MARKER).is_file():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def find_repo_root() -> Path:
    """Locate the Swanlake repo root. Cached after first call.

    Raises CompatError if the root cannot be resolved.
    """
    global _repo_root
    if _repo_root is not None:
        return _repo_root

    # 1. Explicit env var override.
    env = os.environ.get("SWANLAKE_REPO_ROOT")
    if env:
        candidate = Path(env).expanduser()
        if (candidate / REPO_MARKER).is_file():
            _repo_root = candidate
            return _repo_root

    # 2. Operator config.
    cfg_path = _read_config_repo_path()
    if cfg_path is not None and (cfg_path / REPO_MARKER).is_file():
        _repo_root = cfg_path
        return _repo_root

    # 3. Walk up from this file.
    here = Path(__file__).resolve().parent
    walked = _walk_up_for_marker(here)
    if walked is not None:
        _repo_root = walked
        return _repo_root

    raise CompatError(
        "Could not locate Swanlake repo root. "
        "Set SWANLAKE_REPO_ROOT env var or run from inside a Swanlake clone."
    )


def _load_module(name: str, path: Path) -> ModuleType:
    """Load a Python file under an arbitrary module name via importlib."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise CompatError(f"Could not build import spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    # Register so internal `from <name> import ...` would work if the
    # module ever did self-imports.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def status_segment_module() -> ModuleType:
    """Return the loaded tools/status-segment.py as an importable module.

    Cached after first call.
    """
    global _status_segment_mod
    if _status_segment_mod is None:
        repo = find_repo_root()
        path = repo / "tools" / "status-segment.py"
        if not path.is_file():
            raise CompatError(f"Missing {path}")
        _status_segment_mod = _load_module("_swanlake_status_segment", path)
    return _status_segment_mod


def loop_closure_metric_module() -> ModuleType:
    """Return the loaded tools/loop-closure-metric.py as an importable module."""
    global _loop_closure_mod
    if _loop_closure_mod is None:
        repo = find_repo_root()
        path = repo / "tools" / "loop-closure-metric.py"
        if not path.is_file():
            raise CompatError(f"Missing {path}")
        _loop_closure_mod = _load_module("_swanlake_loop_closure", path)
    return _loop_closure_mod


def reset_cache() -> None:
    """Test hook: drop cached modules + repo root so the next call re-resolves."""
    global _status_segment_mod, _loop_closure_mod, _repo_root
    _status_segment_mod = None
    _loop_closure_mod = None
    _repo_root = None
