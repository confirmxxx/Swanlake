"""`swanlake bench --quick` -- thin wrapper over bench/live-fire-rerun.sh.

Spec MVP T8. `--quick` shells out to <repo>/bench/live-fire-rerun.sh.
On clean (rc=0) exit, writes the current ISO-UTC timestamp to
~/.swanlake/last-bench so `swanlake status`'s bench dimension picks
it up.

`--full` is intentionally a stub for v0.2: the PyRIT + Garak harness
runs out-of-tree at /tmp/swanlake-pyrit-garak-bench-* per the spec, and
wiring it into the CLI is deferred until the harness is more stable.
Returns NOT_IMPLEMENTED (3) so the operator can distinguish "feature
missing" from a real benchmark failure.

Pass/fail counts are extracted from the script's stdout. The script
emits `[N] <slug>  <verdict>  http=...  bytes=...` lines; we count
verdicts of PASS / BLOCKED / HOOK_ERROR / FETCH_FAILED. Blocked is
the desirable outcome for prompt-injection corpora (the hook caught
it); we expose all four counts in the audit row.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from swanlake import _compat
from swanlake import state as _state
from swanlake.exit_codes import ALARM, CLEAN, NOT_IMPLEMENTED, USAGE
from swanlake.output import eprint, print_json, print_line


LAST_BENCH_FILENAME = "last-bench"
LIVE_FIRE_REL = Path("bench") / "live-fire-rerun.sh"

# Match `[1] simon-willison-prompt-injection-explained  PASS  http=...`
# style lines emitted by live-fire-rerun.sh's text summary.
_SUMMARY_LINE_RE = re.compile(
    r"^\s*\[\d+\]\s+\S+\s+(?P<verdict>[A-Z_]+(?:\([^)]*\))?)\s+"
)


def _parse_counts(stdout: str) -> dict[str, int]:
    """Count PASS / BLOCKED / HOOK_ERROR / FETCH_FAILED verdicts."""
    counts = {
        "pass_count": 0,
        "blocked_count": 0,
        "hook_error_count": 0,
        "fetch_failed_count": 0,
    }
    for line in stdout.splitlines():
        m = _SUMMARY_LINE_RE.match(line)
        if not m:
            continue
        verdict = m.group("verdict")
        if verdict == "PASS":
            counts["pass_count"] += 1
        elif verdict == "BLOCKED":
            counts["blocked_count"] += 1
        elif verdict.startswith("HOOK_ERROR"):
            counts["hook_error_count"] += 1
        elif verdict == "FETCH_FAILED":
            counts["fetch_failed_count"] += 1
    return counts


def _write_last_bench() -> Path:
    """Write the current UTC timestamp to ~/.swanlake/last-bench."""
    p = _state.state_path(LAST_BENCH_FILENAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Atomic-ish; the file is one short line and the writer is single-process.
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(ts + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def _resolve_script() -> Path | None:
    try:
        repo = _compat.find_repo_root()
    except _compat.CompatError:
        return None
    candidate = repo / LIVE_FIRE_REL
    return candidate if candidate.exists() else None


def _run_quick(quiet: bool, json_out: bool) -> tuple[int, dict[str, Any]]:
    """Drive bench/live-fire-rerun.sh and return (rc, payload)."""
    script = _resolve_script()
    if script is None:
        eprint(
            "swanlake bench --quick: cannot locate bench/live-fire-rerun.sh "
            "(set SWANLAKE_REPO_ROOT or run from inside a Swanlake clone)"
        )
        return USAGE, {"error": "script_not_found"}

    try:
        # The script writes its own /tmp output file; we capture stdout
        # for the summary table the operator sees and for count parsing.
        proc = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        eprint(f"swanlake bench --quick: {type(e).__name__}: {e}")
        return ALARM, {"error": str(e)}

    if not quiet and not json_out:
        # Surface the script's own output for the operator. JSON mode
        # suppresses it because the parser would otherwise see noise.
        sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)

    counts = _parse_counts(proc.stdout)
    rc = proc.returncode
    payload: dict[str, Any] = {
        "script": str(script),
        "rc": rc,
        **counts,
    }

    # Only stamp last-bench on a clean run -- the spec is explicit about
    # this. A failure leaves the previous (possibly stale) timestamp in
    # place so `swanlake status` continues to report it correctly.
    if rc == 0:
        ts_path = _write_last_bench()
        payload["last_bench"] = str(ts_path)

    return rc, payload


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    json_out = bool(getattr(args, "json", False))
    quick = bool(getattr(args, "quick", False))
    full = bool(getattr(args, "full", False))

    if full:
        msg = (
            "swanlake bench --full not implemented in v0.2 -- run "
            "/tmp/swanlake-pyrit-garak-bench-*/run.sh manually"
        )
        if json_out:
            print_json(
                {"bench": "not_implemented", "mode": "full", "message": msg},
                quiet=quiet,
            )
        else:
            print_line(msg, quiet=False)
        return NOT_IMPLEMENTED

    if not quick:
        # Default to --quick when neither flag is passed. The operator
        # still gets the explicit confirmation in stdout. Could be
        # changed to print --help if we want strictness; the friendlier
        # default matches the spec walkthrough.
        if not quiet:
            print_line("swanlake bench: defaulting to --quick", quiet=False)
        quick = True

    rc, payload = _run_quick(quiet=quiet, json_out=json_out)
    if json_out:
        print_json(payload, quiet=quiet)
    elif not quiet and rc == 0:
        print_line(
            f"bench passed: {payload.get('pass_count', 0)} pass, "
            f"{payload.get('blocked_count', 0)} blocked  "
            f"(stamped {payload.get('last_bench')})",
            quiet=False,
        )
    elif not quiet:
        print_line(
            f"bench script exit {rc}; last-bench NOT updated", quiet=False
        )
    # Pass through the script's exit code so callers see the real result.
    if rc == 2:
        # bench/live-fire-rerun.sh uses `exit 2` for its own setup errors
        # (missing hook script, hook not executable, fetch dependency
        # absent). Surface that as USAGE so a calling shell can tell a
        # configuration problem apart from a real benchmark alarm. The
        # numeric value of USAGE intentionally collides with ALARM (both
        # 2 by argparse convention), but using the named constant keeps
        # the intent legible at the call site.
        return USAGE
    return CLEAN if rc == 0 else ALARM


__all__ = ["run", "_parse_counts"]
