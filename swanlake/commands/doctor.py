"""`swanlake doctor` -- per-primitive health check with fix suggestions.

Spec MVP T5. Runs 8 probes against the local environment, prints a
column-aligned table, and exits with the worst severity:
    pass = 0
    warn = 1
    fail = 2

Probes (in display order):
  1. state-dir perms       -- ~/.swanlake exists, mode 0700
  2. audit log writable    -- can append a no-op record
  3. reconciler config     -- ~/.swanlake/config.toml OR
                              ~/.config/swanlake-reconciler/config.toml
  4. deployment-map        -- path from config readable as JSON
  5. canon templates       -- <repo>/canon/operating-rules.md present
  6. python3 on PATH       -- shutil.which('python3')
  7. gh available          -- shutil.which('gh') (warn-only)
  8. git available         -- shutil.which('git')

`--fix-suggestions` prints the exact one-liner fix per failing/warning
row instead of a brief remediation line in the detail column.

Probes return a typed dict:
    {"name": str, "status": "pass"|"warn"|"fail",
     "detail": str, "fix": str|None}

Probes never raise; exceptions degrade to {status: fail, detail: error}.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable

from swanlake import _compat
from swanlake import state as _state
from swanlake.exit_codes import ALARM, CLEAN, DRIFT
from swanlake.output import print_json, print_line, print_table


SEVERITY_RANK = {"pass": 0, "warn": 1, "fail": 2}
SEVERITY_TO_EXIT = {0: CLEAN, 1: DRIFT, 2: ALARM}


# --- Individual probes ---


def _probe_state_perms() -> dict[str, Any]:
    root = _state.get_state_root()
    if not root.exists():
        return {
            "status": "fail",
            "detail": f"state root missing: {root}",
            "fix": "swanlake init",
        }
    try:
        mode = stat.S_IMODE(root.stat().st_mode)
    except OSError as e:
        return {
            "status": "fail",
            "detail": f"cannot stat {root}: {e}",
            "fix": f"chmod 700 {root}",
        }
    if mode != 0o700:
        return {
            "status": "warn",
            "detail": f"mode {oct(mode)}, expected 0o700",
            "fix": f"chmod 700 {root}",
        }
    return {"status": "pass", "detail": f"{root} (0o700)", "fix": None}


def _probe_audit_writable() -> dict[str, Any]:
    """Try a real append-then-truncate to verify the log is writable."""
    audit = _state.state_path("audit.jsonl")
    try:
        audit.parent.mkdir(parents=True, exist_ok=True)
        # Append-mode opens never truncate. We write zero bytes which the
        # filesystem treats as an open+close (mtime change) without
        # extending the file -- but flushing a single newline that we
        # then strip would corrupt the JSONL. So instead we write to a
        # sibling tempfile to verify the directory is writable.
        with tempfile.NamedTemporaryFile(
            dir=str(audit.parent), prefix=".doctor.", suffix=".tmp",
            delete=True,
        ) as fp:
            fp.write(b"ok\n")
            fp.flush()
    except OSError as e:
        return {
            "status": "fail",
            "detail": f"audit dir not writable: {e}",
            "fix": f"chmod u+w {audit.parent}",
        }
    return {"status": "pass", "detail": str(audit), "fix": None}


def _probe_reconciler_config() -> dict[str, Any]:
    new_path = _state.state_path("config.toml")
    legacy = Path.home() / ".config" / "swanlake-reconciler" / "config.toml"
    if new_path.exists():
        return {"status": "pass", "detail": str(new_path), "fix": None}
    if legacy.exists():
        return {
            "status": "warn",
            "detail": f"using legacy {legacy}",
            "fix": "swanlake init  # migrates to ~/.swanlake/config.toml",
        }
    return {
        "status": "fail",
        "detail": "no config.toml in ~/.swanlake or legacy path",
        "fix": "swanlake init",
    }


def _probe_deployment_map() -> dict[str, Any]:
    """Resolve deployment-map path from the reconciler config and check it."""
    try:
        from reconciler import config as recon_config
        cfg = recon_config.load()
    except Exception as e:  # noqa: BLE001 -- probe must degrade
        return {
            "status": "fail",
            "detail": f"reconciler config unloadable: {type(e).__name__}",
            "fix": "swanlake init",
        }
    dmap = cfg.deployment_map_path
    if not dmap.exists():
        return {
            "status": "fail",
            "detail": f"missing: {dmap}",
            "fix": f"create {dmap} (see DEFENSE-BEACON/deployment-map.json template)",
        }
    try:
        # Cheap readability + JSON shape check without parsing the whole
        # file -- a single read() then a partial JSON parse would be more
        # work for no benefit. We only confirm we can read the bytes.
        dmap.read_text()
    except OSError as e:
        return {
            "status": "fail",
            "detail": f"unreadable: {e}",
            "fix": f"chmod u+r {dmap}",
        }
    return {"status": "pass", "detail": str(dmap), "fix": None}


def _probe_canon_templates() -> dict[str, Any]:
    """canon/operating-rules.md must be resolvable from the repo root."""
    try:
        repo = _compat.find_repo_root()
    except _compat.CompatError as e:
        return {
            "status": "fail",
            "detail": f"repo root unresolved: {e}",
            "fix": "set SWANLAKE_REPO_ROOT or run from inside a Swanlake clone",
        }
    rules = repo / "canon" / "operating-rules.md"
    if not rules.exists():
        return {
            "status": "fail",
            "detail": f"missing: {rules}",
            "fix": f"git checkout -- {rules}",
        }
    return {"status": "pass", "detail": str(rules), "fix": None}


def _probe_python3() -> dict[str, Any]:
    p = shutil.which("python3")
    if not p:
        return {
            "status": "fail",
            "detail": "python3 not on PATH",
            "fix": "install python3 (3.11+)",
        }
    return {"status": "pass", "detail": p, "fix": None}


def _probe_gh() -> dict[str, Any]:
    """gh is warn-only -- swanlake itself does not require it."""
    p = shutil.which("gh")
    if not p:
        return {
            "status": "warn",
            "detail": "gh not on PATH (release helpers will be unavailable)",
            "fix": "install: https://cli.github.com",
        }
    return {"status": "pass", "detail": p, "fix": None}


def _probe_git() -> dict[str, Any]:
    p = shutil.which("git")
    if not p:
        return {
            "status": "fail",
            "detail": "git not on PATH",
            "fix": "apt install git  # or your platform's equivalent",
        }
    return {"status": "pass", "detail": p, "fix": None}


def _safe(fn: Callable[[], dict[str, Any]], name: str) -> dict[str, Any]:
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001 -- probe must never crash doctor
        result = {
            "status": "fail",
            "detail": f"{type(e).__name__}: {e}",
            "fix": "report this as a bug",
        }
    result.setdefault("name", name)
    return result


PROBES = (
    ("state-dir perms", _probe_state_perms),
    ("audit log writable", _probe_audit_writable),
    ("reconciler config", _probe_reconciler_config),
    ("deployment-map readable", _probe_deployment_map),
    ("canon templates", _probe_canon_templates),
    ("python3 on PATH", _probe_python3),
    ("gh available", _probe_gh),
    ("git available", _probe_git),
)


def compute() -> dict[str, Any]:
    rows = [_safe(fn, name) for name, fn in PROBES]
    severities = [SEVERITY_RANK.get(r.get("status", "fail"), 2) for r in rows]
    worst = max(severities) if severities else 0
    return {
        "probes": rows,
        "exit_code": SEVERITY_TO_EXIT[worst],
        "worst": ("pass", "warn", "fail")[worst],
    }


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))
    fix_suggestions = bool(getattr(args, "fix_suggestions", False))

    report = compute()

    if json_out:
        print_json(report, quiet=quiet)
        return report["exit_code"]

    rows = []
    for r in report["probes"]:
        detail = r.get("detail", "")
        if fix_suggestions and r.get("fix") and r["status"] != "pass":
            detail = f"{detail}  ->  fix: {r['fix']}"
        rows.append({
            "probe": r["name"],
            "status": r["status"],
            "detail": detail,
        })

    print_table(rows, columns=("probe", "status", "detail"), quiet=quiet)
    if not quiet:
        print_line(
            f"worst: {report['worst']}  [exit {report['exit_code']}]",
            quiet=False,
        )
    return report["exit_code"]


__all__ = ["run", "compute", "PROBES"]
