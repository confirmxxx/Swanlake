"""`swanlake beacon verify` -- thin wrapper over `swanlake verify` with REMOTE dispatch.

Per spec D2: single source of truth for marker-grep stays in
swanlake.commands.verify.compute. This wrapper adds REMOTE-type dispatch
and per-surface scoping. The implementation is ~120 LOC, not a parallel
codepath: LOCAL surfaces delegate to verify.compute(only_surface=<id>),
REMOTE surfaces dispatch by type to per-checker functions.

REMOTE checkers (spec section 7):
  notion         : urllib GET against api.notion.com with bearer from
                   SWANLAKE_NOTION_TOKEN env var (D11). Token absence
                   returns `unconfigured` with a manual-fallback hint.
  supabase-env   : delegate to `supabase secrets list` if available;
                   absent CLI yields `manual`.
  vercel-env     : delegate to `vercel env ls` if available; absent CLI
                   yields `manual`.
  github-public  : urllib GET against api.github.com (no PAT, public
                   endpoint only).
  claude-routine : always `manual` -- no API path by design (D8).

Per-surface status mirrors `swanlake verify`:
  intact / drifted / missing / unreadable / manual / unconfigured / remote-skip

The matched canary literal is NEVER echoed -- regex match objects are
discarded; only the boolean `did_match` flows into output. (R6.)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

from swanlake import coverage as _cov
from swanlake.commands import verify as _local_verify
from swanlake.commands.beacon._registry import (
    SCOPE_LOCAL,
    SCOPE_REMOTE,
    get_type,
    infer_type,
)
from swanlake.commands.beacon._surfaces import (
    discover_surfaces_yaml,
    load_surfaces,
)
from swanlake.exit_codes import CLEAN, DRIFT, USAGE
from swanlake.output import eprint, print_json, print_line, print_table


NOTION_TOKEN_ENV = "SWANLAKE_NOTION_TOKEN"


def _attrib_pattern(surface_id: str) -> re.Pattern[str]:
    return re.compile(
        r"beacon-attrib-" + re.escape(surface_id) + r"-[A-Za-z0-9]{8}\b"
    )


def _check_notion(surface_id: str, target: str | None) -> dict[str, Any]:
    """Verify a Notion-page surface via api.notion.com.

    Requires SWANLAKE_NOTION_TOKEN. The token is a read-only integration
    token scoped to the surface pages -- never a workspace-admin token.
    Token absence returns `unconfigured` with a manual-fallback hint.

    `target` is expected to be a Notion page-id or page URL; if it
    contains `notion.so/<id>` we extract the id. Empty target returns
    `manual` (cannot verify without knowing what to fetch).
    """
    token = os.environ.get(NOTION_TOKEN_ENV)
    if not token:
        return {
            "status": "unconfigured",
            "hint": (
                f"set {NOTION_TOKEN_ENV}=<read-only integration token> or "
                f"run 'swanlake beacon checklist --surface {surface_id}' "
                "for the manual-paste fallback"
            ),
        }
    if not target:
        return {
            "status": "manual",
            "hint": (
                f"surface {surface_id!r} has no target identifier; "
                "add `target: <notion-page-url>` to surfaces.yaml"
            ),
        }
    # Extract page id from URL or use raw id.
    page_id = target.rsplit("/", 1)[-1].split("?", 1)[0].split("-")[-1]
    if not page_id:
        return {"status": "manual", "hint": f"could not parse page id from {target!r}"}

    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Notion-Version", "2022-06-28")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return {
            "status": "unreadable",
            "hint": f"notion API returned HTTP {e.code}",
        }
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {
            "status": "unreadable",
            "hint": f"notion API call failed: {type(e).__name__}",
        }

    pat = _attrib_pattern(surface_id)
    if pat.search(body):
        # Critical: the match object is discarded; only the boolean flows.
        return {"status": "intact"}
    return {"status": "drifted"}


def _check_supabase_env(surface_id: str, target: str | None) -> dict[str, Any]:
    """Existence-only check via `supabase secrets list --project-ref <ref>`.

    `target` carries `<KEY>@<project-ref>` (or `<KEY>` if the project ref
    lives elsewhere). Existence-only by design: we never read the value
    (the value IS the canary).
    """
    if not shutil.which("supabase"):
        return {"status": "manual", "hint": "supabase CLI not on PATH"}
    if not target or "@" not in target:
        return {
            "status": "manual",
            "hint": (
                f"surface {surface_id!r} target must be `<KEY>@<project-ref>`; "
                "got " + repr(target)
            ),
        }
    key, project_ref = target.split("@", 1)
    try:
        proc = subprocess.run(
            ["supabase", "secrets", "list", "--project-ref", project_ref],
            capture_output=True, text=True, check=False, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"status": "unreadable", "hint": f"supabase CLI failed: {type(e).__name__}"}
    if proc.returncode != 0:
        return {"status": "unreadable", "hint": f"supabase exit {proc.returncode}"}
    # Existence check: line-grep for the key.
    for line in proc.stdout.splitlines():
        if key in line.split():
            return {"status": "intact"}
    return {"status": "drifted"}


def _check_vercel_env(surface_id: str, target: str | None) -> dict[str, Any]:
    """Same shape as supabase but via `vercel env ls`."""
    if not shutil.which("vercel"):
        return {"status": "manual", "hint": "vercel CLI not on PATH"}
    if not target:
        return {"status": "manual", "hint": "vercel env target unset"}
    key = target.split("@", 1)[0]
    try:
        proc = subprocess.run(
            ["vercel", "env", "ls"],
            capture_output=True, text=True, check=False, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"status": "unreadable", "hint": f"vercel CLI failed: {type(e).__name__}"}
    if proc.returncode != 0:
        return {"status": "unreadable", "hint": f"vercel exit {proc.returncode}"}
    for line in proc.stdout.splitlines():
        if key in line.split():
            return {"status": "intact"}
    return {"status": "drifted"}


def _check_github_public(surface_id: str, target: str | None) -> dict[str, Any]:
    """Verify a public-repo file via api.github.com unauthenticated.

    `target` is `<owner>/<repo>:<path>` (e.g. `acme/foo:README.md`).
    No PAT required -- the public-content endpoint is anonymous.
    """
    if not target or ":" not in target:
        return {
            "status": "manual",
            "hint": (
                f"surface {surface_id!r} target must be `<owner>/<repo>:<path>`; "
                "got " + repr(target)
            ),
        }
    repo_part, path_part = target.split(":", 1)
    if "/" not in repo_part:
        return {"status": "manual", "hint": "repo must be `<owner>/<repo>`"}
    url = f"https://api.github.com/repos/{repo_part}/contents/{path_part}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github.v3.raw")
    req.add_header("User-Agent", "swanlake-beacon-verify/0.3")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return {"status": "unreadable", "hint": f"github API returned HTTP {e.code}"}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"status": "unreadable", "hint": f"github API call failed: {type(e).__name__}"}

    if _attrib_pattern(surface_id).search(body):
        return {"status": "intact"}
    return {"status": "drifted"}


def _check_claude_routine(surface_id: str, target: str | None) -> dict[str, Any]:
    """No API path -- routines are export-only (D8). Always manual."""
    hint = (
        "routines have no API path in v0.3; export the routine prompt "
        "from the UI and grep for the surface attribution marker by hand"
    )
    if target:
        hint = f"open {target} and confirm the beacon block is present"
    return {"status": "manual", "hint": hint}


_REMOTE_DISPATCH = {
    "notion": _check_notion,
    "supabase-env": _check_supabase_env,
    "vercel-env": _check_vercel_env,
    "github-public": _check_github_public,
    "claude-routine": _check_claude_routine,
}


def _surface_target(surface_id: str) -> str | None:
    """Look up the target identifier for `surface_id` from surfaces.yaml.

    Best-effort: returns None if the file is absent or the surface is
    plain-id form (no annotation).
    """
    try:
        from swanlake import _compat
        repo_root = _compat.find_repo_root()
        path = discover_surfaces_yaml(repo_root)
    except Exception:
        path = discover_surfaces_yaml(None)
    if path is None:
        return None
    try:
        for spec in load_surfaces(path):
            if spec.surface_id == surface_id:
                return spec.target
    except OSError:
        pass
    return None


def _surface_type(surface_id: str) -> str:
    """Return the type_id for `surface_id` -- check surfaces.yaml then prefix."""
    try:
        from swanlake import _compat
        repo_root = _compat.find_repo_root()
        path = discover_surfaces_yaml(repo_root)
    except Exception:
        path = discover_surfaces_yaml(None)
    if path is not None:
        try:
            for spec in load_surfaces(path):
                if spec.surface_id == surface_id:
                    return spec.type_id
        except OSError:
            pass
    return infer_type(surface_id)


def compute(only_surface: str | None = None, since: str | None = None) -> dict[str, Any]:
    """Build the verify report.

    Splits surfaces by scope: LOCAL surfaces flow to swanlake.commands.verify.compute;
    REMOTE surfaces flow through the per-type checker.
    """
    cov = _cov.list_surfaces()
    surfaces_map = cov.get("surfaces") or {}

    if only_surface:
        if only_surface not in surfaces_map:
            return {
                "surfaces": [],
                "exit_code": USAGE,
                "error": f"surface {only_surface!r} not in coverage",
            }
        surfaces_to_check = [only_surface]
    else:
        surfaces_to_check = sorted(surfaces_map.keys())

    rows: list[dict[str, Any]] = []
    worst = 0
    for sid in surfaces_to_check:
        type_id = _surface_type(sid)
        st = get_type(type_id)
        if st is None:
            rows.append({
                "surface": sid,
                "type": type_id,
                "status": "unknown-type",
            })
            worst = max(worst, 1)
            continue

        if st.is_local:
            local_report = _local_verify.compute(only_surface=sid, since=since)
            local_rows = local_report.get("surfaces") or []
            if local_rows:
                row = local_rows[0]
                status = row.get("status", "drifted")
            else:
                status = "missing"
            rows.append({
                "surface": sid,
                "type": type_id,
                "status": status,
            })
            if status != "intact":
                worst = max(worst, 1)
        else:
            checker = _REMOTE_DISPATCH.get(type_id)
            if checker is None:
                rows.append({
                    "surface": sid,
                    "type": type_id,
                    "status": "unknown-type",
                })
                worst = max(worst, 1)
                continue
            target = _surface_target(sid)
            result = checker(sid, target)
            entry = {
                "surface": sid,
                "type": type_id,
                "status": result["status"],
            }
            if "hint" in result:
                entry["hint"] = result["hint"]
            rows.append(entry)
            if result["status"] not in ("intact", "manual", "unconfigured"):
                worst = max(worst, 1)

    return {
        "surfaces": rows,
        "exit_code": CLEAN if worst == 0 else DRIFT,
    }


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))
    only = getattr(args, "surface", None)
    since = getattr(args, "since", None)

    report = compute(only_surface=only, since=since)
    if "error" in report:
        eprint(f"swanlake beacon verify: {report['error']}")
        return report["exit_code"]

    if json_out:
        print_json(report, quiet=quiet)
        return report["exit_code"]

    table_rows = [
        {
            "surface": r["surface"],
            "type": r.get("type", ""),
            "status": r["status"],
        }
        for r in report["surfaces"]
    ]
    print_table(
        table_rows,
        columns=("surface", "type", "status"),
        quiet=quiet,
    )
    # Surface hints to stderr (non-INTACT entries usually carry one).
    for r in report["surfaces"]:
        if r.get("hint"):
            eprint(f"  {r['surface']}: {r['hint']}")
    if not quiet:
        word = "INTACT" if report["exit_code"] == CLEAN else "DRIFT"
        print_line(
            f"beacon verify: {word}  [exit {report['exit_code']}]",
            quiet=False,
        )
    return report["exit_code"]


__all__ = ["run", "compute", "NOTION_TOKEN_ENV"]
