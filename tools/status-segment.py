#!/usr/bin/env python3
"""Swanlake status segment.

Terse health indicator for a shell status line, Starship segment, or Claude
Code status-line hook. Reads local posture state + today's hit logs and
emits a single short string. Exits 0 always so a bad state never breaks the
status line itself.

Output grammar:
    All clean + fresh                             -> "🛡"
    Unknown staleness (no last-run file yet)       -> "🛡?"
    Posture stale (yellow band)                    -> "🛡stale:Nd"
    Posture stale (red band)                       -> "🛡!stale:Nd"
    Any real hits today                           -> "🛡canary:N"
    Combined                                       -> "🛡!stale:8d,canary:1"

Counting semantics: each per-dir counter reports *real detections*, not
hook-fire volume. A clean day produces zero flags even if the underlying
hook fired hundreds of times (e.g. content-safety inspecting every WebFetch).
The previous implementation counted every non-empty JSONL line, which made
"inject:40" mean "the hook ran 40 times today" rather than "40 prompt
injections were caught" — a misleading-UI bug. Per-dir hit predicates now
classify each line and only real hits are counted.

Configurable via environment variables:

    SWANLAKE_LAST_RUN       Path to ISO-UTC timestamp file written by the
                            routine or manual-fire step.
                            Default: ~/.claude/.last-watchdog-run

    SWANLAKE_TICK           Path to ISO-UTC timestamp file written by the
                            local systemd timer (fallback if LAST_RUN is
                            missing).
                            Default: ~/.claude/.watchdog-tick

    SWANLAKE_CANARY_HITS    Directory of YYYY-MM-DD.jsonl files from the
                            canary-match hook.
                            Default: ~/.claude/canary-hits

    SWANLAKE_EXFIL_HITS     Directory of YYYY-MM-DD.jsonl files from the
                            exfil-monitor hook.
                            Default: ~/.claude/exfil-alerts

    SWANLAKE_CONTENT_HITS   Directory of YYYY-MM-DD.jsonl files from the
                            content-safety-check hook.
                            Default: ~/.claude/content-safety

    SWANLAKE_STALE_YELLOW   Days of staleness that triggers the yellow
                            band. Default: 2

    SWANLAKE_STALE_RED      Days of staleness that triggers the red band
                            and the "!stale" prefix.
                            Default: 7

    SWANLAKE_STATUS_STYLE   "default" | "silent". Silent mode emits nothing
                            when posture is clean (useful for PS1 users who
                            only want the glyph to appear on issues).

    SWANLAKE_STATUS_VERBOSITY  "default" | "full". In "full" mode each per-
                               dir counter renders both real-hit count and
                               total fires today as "label:H/F" (e.g.
                               "inject:0/40"). Default mode shows only the
                               real-hit count and suppresses the flag at
                               zero, matching the historical glyph grammar.

Pairs with:
    defense-beacon/reference/canary-match.sh  (writes CANARY_HITS)
    other local hooks (exfil-monitor, content-safety-check) write the other
    two dirs under the same YYYY-MM-DD.jsonl pattern.

See tools/README.md for integration examples.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple

HOME = Path.home()

LAST_RUN = Path(os.environ.get(
    "SWANLAKE_LAST_RUN", str(HOME / ".claude/.last-watchdog-run")
))
TICK = Path(os.environ.get(
    "SWANLAKE_TICK", str(HOME / ".claude/.watchdog-tick")
))
CANARY_DIR = Path(os.environ.get(
    "SWANLAKE_CANARY_HITS", str(HOME / ".claude/canary-hits")
))
EXFIL_DIR = Path(os.environ.get(
    "SWANLAKE_EXFIL_HITS", str(HOME / ".claude/exfil-alerts")
))
CONTENT_DIR = Path(os.environ.get(
    "SWANLAKE_CONTENT_HITS", str(HOME / ".claude/content-safety")
))

try:
    YELLOW = int(os.environ.get("SWANLAKE_STALE_YELLOW", "2"))
    RED = int(os.environ.get("SWANLAKE_STALE_RED", "7"))
except ValueError:
    YELLOW, RED = 2, 7

STYLE = os.environ.get("SWANLAKE_STATUS_STYLE", "default")
VERBOSITY = os.environ.get("SWANLAKE_STATUS_VERBOSITY", "default")


# --- Per-dir hit predicates -------------------------------------------------
#
# Each predicate receives a parsed JSON object (one log line) and returns
# True iff that line represents a *real* detection rather than a routine
# hook fire. Schemas as observed in the field:
#
#   content-safety/*.jsonl  — every fire logs {"block": bool, "score": int,
#       "findings": [...], ...}. Real hit = block is true OR score > 0 OR
#       findings is non-empty. Most days are 100% noise (every WebFetch).
#
#   canary-hits/*.jsonl     — only writes a line when the canary-match hook
#       sees one or more tokens in tool I/O. Each line carries a non-empty
#       "hits" array. Real hit = hits is a non-empty list.
#
#   exfil-alerts/*.jsonl    — every line carries "severity" in {"block",
#       "warn", "info"}. Real hit = severity in {"block", "warn"}; "info"
#       (if/when emitted) is treated as noise.
#
# Each predicate must be defensive: malformed lines and missing fields must
# return False, never raise. The caller swallows JSON parse errors and
# treats them as non-hits (a corrupt log line is not an attack signal).


def _content_safety_hit(rec: dict) -> bool:
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
    return isinstance(hits, list) and len(hits) > 0


def _exfil_hit(rec: dict) -> bool:
    sev = rec.get("severity")
    return sev in ("block", "warn")


HitPredicate = Callable[[dict], bool]


def count_today(
    dir_path: Path,
    predicate: Optional[HitPredicate] = None,
) -> Tuple[int, int]:
    """Count today's lines under dir_path.

    Returns (hits, fires) where:
        fires = total non-empty lines (every hook invocation logged today)
        hits  = lines for which predicate(parsed_json) is True

    If predicate is None, every non-empty line counts as a hit (legacy
    behavior). If a line is not valid JSON, it counts as a fire but never
    as a hit — corruption is not a detection signal. Missing file: (0, 0).
    On unexpected I/O error: (0, 0). Never raises.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = dir_path / f"{today}.jsonl"
    if not f.exists():
        return (0, 0)
    try:
        text = f.read_text()
    except Exception:
        return (0, 0)
    fires = 0
    hits = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fires += 1
        if predicate is None:
            hits += 1
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        try:
            if predicate(rec):
                hits += 1
        except Exception:
            # A buggy predicate must not break the status line.
            continue
    return (hits, fires)


def _format_counter(label: str, hits: int, fires: int) -> Optional[str]:
    """Render a single counter according to verbosity. Returns None to
    suppress the flag entirely (default mode at zero hits)."""
    if VERBOSITY == "full":
        # Always show, even at zero — the operator wants the full picture.
        return f"{label}:{hits}/{fires}"
    if hits > 0:
        return f"{label}:{hits}"
    return None


def staleness_days() -> int:
    """Return staleness in whole days, or -1 if no timestamp source found."""
    for src in (LAST_RUN, TICK):
        if src.exists():
            try:
                raw = src.read_text().strip()
                # Accept either a first-line ISO timestamp or the whole
                # file as a single timestamp.
                raw = raw.splitlines()[0] if "\n" in raw else raw
                raw = raw[:32]
                ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                delta = datetime.now(timezone.utc) - ts
                return max(delta.days, 0)
            except Exception:
                continue
    return -1


def build_flags() -> list[str]:
    flags: list[str] = []
    stale = staleness_days()
    if stale < 0:
        flags.append("?")
    elif stale >= RED:
        flags.append(f"!stale:{stale}d")
    elif stale >= YELLOW:
        flags.append(f"stale:{stale}d")

    canary_hits, canary_fires = count_today(CANARY_DIR, _canary_hit)
    flag = _format_counter("canary", canary_hits, canary_fires)
    if flag is not None:
        flags.append(flag)

    exfil_hits, exfil_fires = count_today(EXFIL_DIR, _exfil_hit)
    flag = _format_counter("exfil", exfil_hits, exfil_fires)
    if flag is not None:
        flags.append(flag)

    content_hits, content_fires = count_today(CONTENT_DIR, _content_safety_hit)
    flag = _format_counter("inject", content_hits, content_fires)
    if flag is not None:
        flags.append(flag)

    return flags


def main() -> int:
    try:
        flags = build_flags()
        # The "?" unknown-staleness flag alone is not an alarm, just
        # informational; treat it as the "clean but unknown" state.
        if flags == ["?"]:
            # First-run case before any tick / last-run file exists.
            # Render the question mark glyph to signal "wire me up".
            sys.stdout.write("🛡?")
        elif not flags:
            if STYLE == "silent":
                sys.stdout.write("")
            else:
                sys.stdout.write("🛡")
        else:
            sys.stdout.write("🛡" + ",".join(flags))
    except Exception:
        # Never break the caller's status line.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
