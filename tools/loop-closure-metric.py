#!/usr/bin/env python3
"""Swanlake loop-closure metric.

Answers two different questions the operator currently conflates:

    Q1. Does the defense layer fire when something hostile shows up?
        (Already answered by status-segment.py — canary:N, exfil:N, inject:N.)

    Q2. When the defense fires, does the operator close the loop with a
        durable hardening artifact (new hook rule, new deny entry, new
        test fixture, new doc note) or does the alert decay into a sticky
        note that nothing was learned from?

Q2 is the only one that distinguishes a defense that *works* from a
defense that fires-and-forgets. This metric tracks it as a ratio:

    ratio = artifacts_produced / max(events_caught, 1)

A high ratio (1.0+) means each event spawned a learning artifact.
A low ratio (< 0.3) means alerts are accumulating without follow-up —
the defense is becoming theater, the operator should redesign before
the noise crowds out signal.

Inputs:
    ~/.claude/canary-hits/<date>.jsonl    — canary-match.sh output. Real
        hit = non-empty 'hits' AND not self_edit_noise.
    ~/.claude/content-safety/<date>.jsonl — content-safety-check.sh output.
        Real hit = block True OR score > 0 OR non-empty findings.
    ~/.claude/exfil-alerts/<date>.jsonl   — exfil-monitor.sh output.
        Real hit = severity in {'block', 'warn'}.

Hardening artifacts (counted across the same UTC day):
    1. Git commits in ~/projects/Swanlake/ and ~/projects/DEFENSE-BEACON/
       matching ^(fix|feat|chore|test|docs)(\\([^)]+\\))?:.* — the standard
       conventional-commit shape used in both repos.
    2. New deny-list entries added to ~/.claude/settings.json — counted by
       diffing the line count of the deny array against the previous day's
       snapshot; a positive delta is one artifact per new entry.
    3. New files added under ~/.claude/hooks/ (mtime in window).

Outputs:
    --rollup           Compute today's metric, print as JSON to stdout, and
                       write to ~/.claude/loop-closure/<date>.json. Idempotent:
                       re-running on the same day overwrites.
    --report [--days N] Aggregate the last N days (default 7) from the
                       per-day rollups; print summary table + ratio.
    --status-flag      Emit the status-segment flag string ("closure:NN%")
                       if the 7-day rolling ratio is below the threshold,
                       else exit 0 with no output. Threshold default 30%.
    (no flag)          Same as --rollup.

Configurable via environment variables:
    SWANLAKE_CANARY_HITS, SWANLAKE_CONTENT_HITS, SWANLAKE_EXFIL_HITS
        — same as status-segment.py; share a dir if you want.
    SWANLAKE_ROLLUP_DIR
        — default ~/.claude/loop-closure
    SWANLAKE_HOOKS_DIR
        — default ~/.claude/hooks
    SWANLAKE_SETTINGS_FILE
        — default ~/.claude/settings.json
    SWANLAKE_HARDENING_REPOS
        — comma-separated absolute paths of git repos to scan for commits.
          Default: ~/projects/Swanlake,~/projects/DEFENSE-BEACON
    SWANLAKE_CLOSURE_THRESHOLD
        — float in [0, 1]. Default 0.30. Status-flag fires below this.

Exit code is always 0 in --status-flag mode (status lines must not break).
Exit code is 0 on success and 1 on argument errors otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

HOME = Path.home()

CANARY_DIR = Path(os.environ.get(
    "SWANLAKE_CANARY_HITS", str(HOME / ".claude/canary-hits")))
CONTENT_DIR = Path(os.environ.get(
    "SWANLAKE_CONTENT_HITS", str(HOME / ".claude/content-safety")))
EXFIL_DIR = Path(os.environ.get(
    "SWANLAKE_EXFIL_HITS", str(HOME / ".claude/exfil-alerts")))
ROLLUP_DIR = Path(os.environ.get(
    "SWANLAKE_ROLLUP_DIR", str(HOME / ".claude/loop-closure")))
HOOKS_DIR = Path(os.environ.get(
    "SWANLAKE_HOOKS_DIR", str(HOME / ".claude/hooks")))
SETTINGS_FILE = Path(os.environ.get(
    "SWANLAKE_SETTINGS_FILE", str(HOME / ".claude/settings.json")))
REPOS = [Path(p) for p in os.environ.get(
    "SWANLAKE_HARDENING_REPOS",
    f"{HOME}/projects/Swanlake,{HOME}/projects/DEFENSE-BEACON",
).split(",") if p.strip()]

try:
    THRESHOLD = float(os.environ.get("SWANLAKE_CLOSURE_THRESHOLD", "0.30"))
except ValueError:
    THRESHOLD = 0.30

CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(fix|feat|chore|test|docs|refactor|perf|build|ci|style)(\([^)]+\))?:.+"
)


# --- Real-hit predicates (intentionally identical to status-segment.py) ---
# Duplicated here rather than imported to keep this script standalone — the
# rollup may run in environments where the two scripts diverge in ownership.
# If they do diverge, the test suite catches it.

def _is_interactive_session(rec: dict) -> bool:
    """Return False if the record is from a non-interactive context (bench
    harness, CI fixture, direct shell invocation). The Claude Code hook
    environment always populates session_id with a non-empty UUID; bench
    runners and ad-hoc test invocations write the field with an empty
    string. Records lacking the field entirely (legacy rows or external
    producers) are treated as interactive — we only filter when the field
    is present and explicitly empty, which is the bench-harness signature.

    Rationale: bench fixtures fire detectors *by design* on synthetic
    hostile content. Counting them as "events caught" inflates the
    denominator of the closure ratio and turns the metric into a measure
    of bench activity rather than real-world drift response."""
    sid = rec.get("session_id")
    if sid is None:
        return True
    return bool(sid)


def _content_safety_hit(rec: dict) -> bool:
    if not _is_interactive_session(rec):
        return False
    if rec.get("block") is True:
        return True
    score = rec.get("score")
    if isinstance(score, (int, float)) and score > 0:
        return True
    findings = rec.get("findings")
    if isinstance(findings, list) and findings:
        return True
    return False


def _canary_hit(rec: dict) -> bool:
    hits = rec.get("hits")
    if not (isinstance(hits, list) and len(hits) > 0):
        return False
    if rec.get("self_edit_noise") is True:
        return False
    if not _is_interactive_session(rec):
        return False
    return True


def _exfil_hit(rec: dict) -> bool:
    if not _is_interactive_session(rec):
        return False
    return rec.get("severity") in ("block", "warn")


# --- I/O helpers ---

def _iter_jsonl(path: Path) -> Iterable[dict]:
    """Yield parsed JSON dicts from a JSONL file. Tolerates malformed
    lines, missing files, non-dict JSON. Never raises."""
    if not path.exists():
        return
    try:
        text = path.read_text()
    except Exception:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            yield rec


def count_real_hits(dir_path: Path, predicate, target_date: date) -> int:
    """Count predicate-passing records in <dir>/<YYYY-MM-DD>.jsonl."""
    f = dir_path / f"{target_date.isoformat()}.jsonl"
    n = 0
    for rec in _iter_jsonl(f):
        try:
            if predicate(rec):
                n += 1
        except Exception:
            continue
    return n


# --- Hardening-artifact counters ---

def count_git_commits(repos: list[Path], target_date: date) -> int:
    """Count commits in each repo where author-date falls on target_date
    AND the message subject matches conventional-commit format. Repos
    that don't exist or aren't git repos contribute 0 silently — they
    may be intentionally absent on a particular host."""
    n = 0
    since = target_date.isoformat()
    until = (target_date + timedelta(days=1)).isoformat()
    for repo in repos:
        if not (repo / ".git").exists():
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "log",
                 f"--since={since}", f"--until={until}",
                 "--pretty=format:%s", "--all", "--no-merges"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            subject = line.strip()
            if subject and CONVENTIONAL_COMMIT_RE.match(subject):
                n += 1
    return n


def count_new_hook_files(hooks_dir: Path, target_date: date) -> int:
    """Count regular files in hooks_dir whose mtime lands on target_date.
    A new defensive hook is a hardening artifact; a touched hook is not
    (touch alone doesn't add detection coverage). We approximate "new" via
    mtime falling on the date — good enough for the operator's cadence and
    survives mv-into-place better than ctime."""
    if not hooks_dir.exists() or not hooks_dir.is_dir():
        return 0
    n = 0
    start = datetime.combine(target_date, datetime.min.time(),
                             tzinfo=timezone.utc).timestamp()
    end = start + 86400
    for entry in hooks_dir.iterdir():
        try:
            if not entry.is_file():
                continue
            # Ignore backup/swap/dot files — those are operator hygiene,
            # not defensive coverage.
            name = entry.name
            if name.startswith(".") or name.endswith(".bak") or \
               ".bak-" in name or name.endswith(".swp"):
                continue
            mtime = entry.stat().st_mtime
            if start <= mtime < end:
                n += 1
        except Exception:
            continue
    return n


def _deny_count(settings_file: Path) -> int:
    """Count entries in permissions.deny array. Robust to missing file
    and malformed JSON (returns 0 in either case)."""
    try:
        data = json.loads(settings_file.read_text())
    except Exception:
        return 0
    perms = data.get("permissions") or {}
    deny = perms.get("deny") or []
    if not isinstance(deny, list):
        return 0
    return len(deny)


def deny_delta(settings_file: Path, rollup_dir: Path,
               target_date: date) -> int:
    """Difference between today's deny count and the previous rollup's
    deny count. Returns max(delta, 0) — entries removed don't subtract
    artifact credit (cleanup is a different kind of work). If no
    previous rollup exists, returns 0 (no baseline)."""
    today_count = _deny_count(settings_file)
    # Find the most recent previous rollup (any date < target).
    if not rollup_dir.exists():
        return 0
    prev: Optional[dict] = None
    for entry in sorted(rollup_dir.iterdir(), reverse=True):
        if not entry.name.endswith(".json"):
            continue
        stem = entry.stem
        try:
            d = date.fromisoformat(stem)
        except ValueError:
            continue
        if d >= target_date:
            continue
        try:
            prev = json.loads(entry.read_text())
        except Exception:
            continue
        break
    if prev is None:
        return 0
    prev_count = prev.get("deny_count_snapshot")
    if not isinstance(prev_count, int):
        return 0
    return max(today_count - prev_count, 0)


# --- Rollup composition ---

def compute_rollup(target_date: date) -> dict:
    """Compute the loop-closure rollup for target_date."""
    canary_hits = count_real_hits(CANARY_DIR, _canary_hit, target_date)
    content_hits = count_real_hits(CONTENT_DIR, _content_safety_hit, target_date)
    exfil_hits = count_real_hits(EXFIL_DIR, _exfil_hit, target_date)
    events_caught = canary_hits + content_hits + exfil_hits

    commits = count_git_commits(REPOS, target_date)
    new_hooks = count_new_hook_files(HOOKS_DIR, target_date)
    new_denies = deny_delta(SETTINGS_FILE, ROLLUP_DIR, target_date)
    artifacts_produced = commits + new_hooks + new_denies

    ratio = artifacts_produced / max(events_caught, 1)

    return {
        "date": target_date.isoformat(),
        "events_caught": events_caught,
        "events_breakdown": {
            "canary": canary_hits,
            "content_safety": content_hits,
            "exfil": exfil_hits,
        },
        "artifacts_produced": artifacts_produced,
        "artifacts_breakdown": {
            "commits": commits,
            "new_hooks": new_hooks,
            "new_deny_rules": new_denies,
        },
        "ratio": round(ratio, 4),
        "deny_count_snapshot": _deny_count(SETTINGS_FILE),
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def write_rollup(rollup: dict) -> Path:
    """Write rollup to ~/.claude/loop-closure/<date>.json. Returns path."""
    ROLLUP_DIR.mkdir(parents=True, exist_ok=True)
    out = ROLLUP_DIR / f"{rollup['date']}.json"
    out.write_text(json.dumps(rollup, indent=2, sort_keys=True) + "\n")
    return out


# --- Reporting ---

def aggregate_window(end_date: date, days: int) -> dict:
    """Sum events + artifacts across [end_date - days + 1, end_date].
    Pulls from per-day rollups where present; computes on the fly for
    days that have no rollup yet (so a fresh install that calls --report
    still produces meaningful output)."""
    total_events = 0
    total_artifacts = 0
    days_covered = 0
    days_with_data = 0
    for offset in range(days):
        d = end_date - timedelta(days=offset)
        rollup_path = ROLLUP_DIR / f"{d.isoformat()}.json"
        rec = None
        if rollup_path.exists():
            try:
                rec = json.loads(rollup_path.read_text())
            except Exception:
                rec = None
        if rec is None:
            rec = compute_rollup(d)
        days_covered += 1
        ev = int(rec.get("events_caught") or 0)
        ar = int(rec.get("artifacts_produced") or 0)
        total_events += ev
        total_artifacts += ar
        if ev or ar:
            days_with_data += 1
    ratio = total_artifacts / max(total_events, 1)
    return {
        "window_days": days,
        "end_date": end_date.isoformat(),
        "days_covered": days_covered,
        "days_with_data": days_with_data,
        "total_events": total_events,
        "total_artifacts": total_artifacts,
        "ratio": round(ratio, 4),
    }


# --- CLI ---

def cmd_rollup() -> int:
    today = datetime.now(timezone.utc).date()
    rollup = compute_rollup(today)
    write_rollup(rollup)
    print(json.dumps(rollup, indent=2, sort_keys=True))
    return 0


def cmd_report(days: int) -> int:
    today = datetime.now(timezone.utc).date()
    summary = aggregate_window(today, days)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def cmd_status_flag() -> int:
    today = datetime.now(timezone.utc).date()
    try:
        summary = aggregate_window(today, 7)
    except Exception:
        return 0
    # Only show the flag when there's enough activity to be meaningful AND
    # the ratio is below threshold. Zero events = no signal, zero flag.
    if summary["total_events"] < 3:
        return 0
    if summary["ratio"] >= THRESHOLD:
        return 0
    pct = int(round(summary["ratio"] * 100))
    sys.stdout.write(f"closure:{pct}%")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Swanlake loop-closure metric",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--rollup", action="store_true",
                   help="Compute and write today's rollup (default).")
    g.add_argument("--report", action="store_true",
                   help="Aggregate report over the last --days days.")
    g.add_argument("--status-flag", action="store_true",
                   help="Emit closure:NN%% flag if 7-day ratio below threshold.")
    p.add_argument("--days", type=int, default=7,
                   help="Window for --report (default 7).")
    args = p.parse_args(argv)

    if args.report:
        if args.days < 1:
            print("--days must be >= 1", file=sys.stderr)
            return 1
        return cmd_report(args.days)
    if args.status_flag:
        return cmd_status_flag()
    return cmd_rollup()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
