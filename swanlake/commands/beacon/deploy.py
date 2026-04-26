"""`swanlake beacon deploy` -- LOCAL deploy with the 12-step safety machine.

Mutates one local file per invocation. The safety machine lives in
swanlake.commands.beacon._local.run_local_deploy(); this module is the
CLI dispatch + history-append + REMOTE-refusal shell.

REMOTE surfaces are refused with a clear hint pointing at
`swanlake beacon checklist --surface <id>` (the spec hard-rules
auto-deploy to REMOTE; see N1 + the load-bearing decision in §1).

Exit codes:
  0  deployed
  1  REMOTE surface (checklist printed instead) OR dry-run
  2  aborted (any reason in the 12-step machine)
  3  not-implemented (surface type unknown)
"""
from __future__ import annotations

from typing import Any

from swanlake.commands.beacon import _history, _local, _surfaces
from swanlake.commands.beacon._registry import (
    METHOD_LOCAL,
    SCOPE_LOCAL,
    SCOPE_REMOTE,
    get_type,
    infer_type,
)
from swanlake.exit_codes import ALARM, CLEAN, DRIFT, NOT_IMPLEMENTED
from swanlake.output import eprint, print_json, print_line


def _surface_type_from_yaml(surface: str) -> str:
    """Look up the explicit type for `surface` in surfaces.yaml; fall back to prefix."""
    try:
        from swanlake import _compat
        repo_root = _compat.find_repo_root()
        path = _surfaces.discover_surfaces_yaml(repo_root)
    except Exception:
        path = _surfaces.discover_surfaces_yaml(None)
    if path is not None:
        try:
            for spec in _surfaces.load_surfaces(path):
                if spec.surface_id == surface:
                    return spec.type_id
        except OSError:
            pass
    return infer_type(surface)


def _emit_remote_hint(surface: str, type_id: str, json_out: bool, quiet: bool) -> int:
    """Print the REMOTE-refusal message (deploy is forbidden for these types)."""
    hint = (
        f"surface {surface!r} is type {type_id!r} (REMOTE) -- deploy is "
        "checklist-only by design (defense-beacon/SPEC.md Rotation semantics; "
        "spec §1 + N1).\n"
        f"Run: swanlake beacon checklist --surface {surface}"
    )
    if json_out:
        payload: dict[str, Any] = {
            "surface": surface,
            "type": type_id,
            "action": "checklist-only",
            "hint": hint,
        }
        print_json(payload, quiet=quiet)
    else:
        eprint(hint)
    # Best-effort history append.
    try:
        _history.append({
            "op": "deploy",
            "surface": surface,
            "type": type_id,
            "method": "remote-checklist",
            "outcome": "remote-refused-deploy",
        })
    except Exception:
        pass
    # Exit 1 (DRIFT-class signal): "the surface is unbeaconed" is still true.
    return DRIFT


def run(args) -> int:
    surface = getattr(args, "surface", None)
    if not surface:
        eprint("swanlake beacon deploy: SURFACE positional argument required")
        return ALARM
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))
    dry_run = bool(getattr(args, "dry_run", False))
    yes = bool(getattr(args, "yes", False))

    type_id = _surface_type_from_yaml(surface)
    type_obj = get_type(type_id)
    if type_obj is None:
        eprint(
            f"swanlake beacon deploy: surface {surface!r} has unknown type "
            f"{type_id!r}; refusing"
        )
        return NOT_IMPLEMENTED

    if type_obj.is_remote:
        return _emit_remote_hint(surface, type_id, json_out, quiet)

    # LOCAL deploy: hand off to the 12-step safety machine.
    result = _local.run_local_deploy(
        surface=surface,
        type_id=type_id,
        dry_run=dry_run,
        yes=yes,
        quiet=quiet,
    )

    # Append to history (best-effort).
    try:
        _history.append(result.as_history_record())
    except Exception:
        pass

    if json_out:
        payload = {
            "surface": result.surface,
            "type": result.type_id,
            "action": result.outcome,
            "target_path": result.target_path,
            "backup_path": result.backup_path,
            "post_status": result.post_git_status,
            "error": result.error,
        }
        print_json(payload, quiet=quiet)
    else:
        if result.error:
            eprint(f"swanlake beacon deploy: {result.error}")
        if result.outcome == "deployed":
            print_line(
                f"deployed {result.surface} -> {result.target_path}",
                quiet=quiet,
            )
            if result.backup_path:
                print_line(f"backup: {result.backup_path}", quiet=quiet)
            if result.post_git_status:
                print_line("post-write git status:", quiet=quiet)
                for line in result.post_git_status.splitlines():
                    print_line(f"  {line}", quiet=quiet)
            else:
                print_line(
                    f"(no git changes; recheck `git status` in the target repo)",
                    quiet=quiet,
                )
        elif result.outcome == "dry-run":
            print_line(
                f"dry-run: would write to {result.target_path} after operator confirmation",
                quiet=quiet,
            )

    # Exit code map.
    if result.outcome == "deployed":
        return CLEAN
    if result.outcome == "dry-run":
        return CLEAN  # dry-run is informational; no failure
    # Every other outcome is an abort.
    return ALARM


__all__ = ["run"]
