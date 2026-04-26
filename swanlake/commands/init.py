"""`swanlake init` -- first-run bootstrap of the unified state root.

Spec MVP T6. Idempotent. Steps on first run:
  1. ensure_state_root() -- mkdir ~/.swanlake mode 0700
  2. If reconciler config absent (NEITHER ~/.swanlake/config.toml NOR
     legacy ~/.config/swanlake-reconciler/config.toml), call
     reconciler.init.run_init() to capture operator inputs, then move
     the freshly-written config from the legacy path into ~/.swanlake/.
     The legacy reconciler.init writes to its hardcoded path; we relocate
     after the fact rather than fork its prompt logic.
  3. Touch ~/.swanlake/audit.jsonl (empty) if absent.
  4. Touch ~/.swanlake/coverage.json with `{"schema":1,"surfaces":{}}`
     if absent.
  5. Never touch ~/.swanlake/canary-hits/ or ~/.swanlake/canary-strings.txt
     -- spec R3 mitigation.

Re-run prints `already initialised -- nothing to do` and exits 0.

`--add-surface NAME` registers a single surface in coverage.json without
running the full bootstrap. Useful for `swanlake adapt cma` integration.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from swanlake import state as _state
from swanlake.exit_codes import CLEAN, USAGE
from swanlake.output import eprint, print_json, print_line


COVERAGE_FILENAME = "coverage.json"
AUDIT_FILENAME = "audit.jsonl"
CONFIG_FILENAME = "config.toml"
LEGACY_CONFIG = Path.home() / ".config" / "swanlake-reconciler" / "config.toml"

EMPTY_COVERAGE: dict[str, Any] = {"schema": 1, "surfaces": {}}


def _atomic_write(path: Path, text: str) -> None:
    """Atomic write via tempfile + os.replace; matches reconciler/init.py."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _config_present() -> bool:
    """True iff EITHER the new or legacy config file exists."""
    new_p = _state.state_path(CONFIG_FILENAME)
    return new_p.exists() or LEGACY_CONFIG.exists()


def _ensure_audit_log() -> bool:
    """Create an empty audit.jsonl if absent. Return True iff created."""
    p = _state.state_path(AUDIT_FILENAME)
    if p.exists():
        return False
    _atomic_write(p, "")
    return True


def _ensure_coverage() -> bool:
    """Create coverage.json with empty surfaces if absent. Return True iff created."""
    p = _state.state_path(COVERAGE_FILENAME)
    if p.exists():
        return False
    _atomic_write(p, json.dumps(EMPTY_COVERAGE, sort_keys=True, indent=2) + "\n")
    return True


def _relocate_legacy_config() -> bool:
    """If the legacy config exists and the new path does not, copy across.

    The legacy reconciler.init.run_init() always writes to the legacy
    path. After it returns we copy that file into ~/.swanlake/ so future
    swanlake invocations find it via the spec-A3 precedence chain. We
    keep the legacy file untouched -- removing it would break an
    operator who downgrades, and the loader can read either path.

    Returns True iff a copy actually happened.
    """
    new_p = _state.state_path(CONFIG_FILENAME)
    if new_p.exists():
        return False
    if not LEGACY_CONFIG.exists():
        return False
    text = LEGACY_CONFIG.read_text(encoding="utf-8")
    _atomic_write(new_p, text)
    return True


def _add_surface(name: str) -> dict[str, Any]:
    """Register a single surface in coverage.json without running bootstrap.

    Idempotent: re-adding an existing surface updates `source` but does
    not error. Returns the surface entry as written.
    """
    p = _state.state_path(COVERAGE_FILENAME)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = dict(EMPTY_COVERAGE)
    else:
        data = dict(EMPTY_COVERAGE)

    surfaces = data.setdefault("surfaces", {})
    entry = surfaces.get(name) or {}
    entry["source"] = "manual"
    surfaces[name] = entry

    _atomic_write(p, json.dumps(data, sort_keys=True, indent=2) + "\n")
    return entry


# --- Public API used by the CLI dispatcher ---


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))
    add_surface: str | None = getattr(args, "add_surface", None)

    # State root is always ensured first -- everything else relies on it.
    root = _state.ensure_state_root()

    # --add-surface short-circuits the full bootstrap.
    if add_surface:
        if not _state.state_path(COVERAGE_FILENAME).exists():
            # Create a minimal coverage.json so add-surface has somewhere
            # to write. We do not call _ensure_audit_log here: add-surface
            # should be cheap and not provoke unrelated state creation.
            _ensure_coverage()
        entry = _add_surface(add_surface)
        if json_out:
            print_json({"added_surface": add_surface, "entry": entry}, quiet=quiet)
        else:
            print_line(
                f"registered surface {add_surface!r} in coverage.json",
                quiet=quiet,
            )
        return CLEAN

    # Fast path: everything already in place.
    config_was_present = _config_present()
    audit_existed = _state.state_path(AUDIT_FILENAME).exists()
    coverage_existed = _state.state_path(COVERAGE_FILENAME).exists()

    if config_was_present and audit_existed and coverage_existed:
        # Even on a no-op re-run, we still relocate any legacy-only config
        # for forward-compatibility -- but only if the new path is absent.
        # Already covered by config_was_present being True, so nothing to do.
        if json_out:
            print_json(
                {"init": "noop", "state_root": str(root)}, quiet=quiet
            )
        else:
            print_line(
                "already initialised -- nothing to do", quiet=quiet
            )
        return CLEAN

    # Slow path: at least one piece needs creating.
    actions: list[str] = []

    if not config_was_present:
        # Defer to the existing reconciler init wizard for operator
        # prompts. We pass skip_systemd=True because the timer has its
        # own deploy story (spec A2) and we don't want `swanlake init`
        # to re-deploy units the operator may have customised.
        try:
            from reconciler import init as recon_init
            recon_init.run_init(skip_systemd=True)
        except Exception as e:  # noqa: BLE001 -- surface to the operator
            eprint(f"swanlake init: reconciler bootstrap failed: {e}")
            return USAGE
        # Now mirror the legacy config into ~/.swanlake/.
        if _relocate_legacy_config():
            actions.append(f"copied config to {_state.state_path(CONFIG_FILENAME)}")
        else:
            actions.append("config recorded")

    if _ensure_audit_log():
        actions.append(f"created {_state.state_path(AUDIT_FILENAME)}")
    if _ensure_coverage():
        actions.append(f"created {_state.state_path(COVERAGE_FILENAME)}")

    if json_out:
        print_json(
            {"init": "ran", "state_root": str(root), "actions": actions},
            quiet=quiet,
        )
    else:
        for line in actions:
            print_line(line, quiet=quiet)
        # Always end with a hint about systemd, mirroring spec walkthrough.
        if not quiet:
            print_line(
                "next: systemctl --user enable --now swanlake-vault-sync.timer",
                quiet=False,
            )
    return CLEAN


__all__ = ["run", "EMPTY_COVERAGE"]
