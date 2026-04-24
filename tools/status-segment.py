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
    Any alerts today                              -> "🛡canary:N"
    Combined                                       -> "🛡!stale:8d,canary:1"

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

Pairs with:
    defense-beacon/reference/canary-match.sh  (writes CANARY_HITS)
    other local hooks (exfil-monitor, content-safety-check) write the other
    two dirs under the same YYYY-MM-DD.jsonl pattern.

See tools/README.md for integration examples.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

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


def count_today(dir_path: Path) -> int:
    """Count non-empty lines in today's jsonl file under dir_path."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = dir_path / f"{today}.jsonl"
    if not f.exists():
        return 0
    try:
        return sum(1 for line in f.read_text().splitlines() if line.strip())
    except Exception:
        return 0


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

    canary_n = count_today(CANARY_DIR)
    if canary_n > 0:
        flags.append(f"canary:{canary_n}")

    exfil_n = count_today(EXFIL_DIR)
    if exfil_n > 0:
        flags.append(f"exfil:{exfil_n}")

    content_n = count_today(CONTENT_DIR)
    if content_n > 0:
        flags.append(f"inject:{content_n}")

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
