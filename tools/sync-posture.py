#!/usr/bin/env python3
"""Swanlake posture sync.

Bridges the *remote* security-posture signal (the `Last verified:` field
on the Notion Security Posture page, written by the scheduled watchdog
routine) to the *local* status signal that ``tools/status-segment.py``
reads (``~/.claude/.last-watchdog-run``). Without this bridge, the
remote routine can keep Notion fresh while your terminal's shield still
shows ``🛡?`` because the local file never gets a write.

Three modes:

    sync-posture.py [now]
        Offline, zero-network. Writes the current UTC ISO-8601 timestamp
        to ``$SWANLAKE_LAST_RUN`` (default: ``~/.claude/.last-watchdog-run``).
        Idempotent. This is the "I just reviewed the Notion posture page
        by eye, posture is fresh from here" manual confirmation.

    sync-posture.py pull
        Fetches the Notion Security Posture page via the REST API, walks
        its top-level blocks, finds the first ``Last verified: <iso ts>``
        line, and writes that timestamp to ``$SWANLAKE_LAST_RUN``.
        Requires ``NOTION_TOKEN`` (a Notion integration token with read
        access scoped to that page). The page id defaults to a placeholder
        but is overridable via ``$SWANLAKE_POSTURE_PAGE_ID``. Network I/O
        uses ``urllib.request`` — no third-party deps.

    sync-posture.py check
        Reads ``$SWANLAKE_LAST_RUN``, computes staleness in whole days,
        prints ``<state> <N>`` where state is ``fresh`` / ``yellow`` /
        ``red`` / ``unknown``. Same thresholds as ``status-segment.py``.

Environment variables (all optional unless marked):

    SWANLAKE_LAST_RUN          Path to the ISO-UTC timestamp file.
                               Default: ~/.claude/.last-watchdog-run

    SWANLAKE_POSTURE_PAGE_ID   Notion page id for the Security Posture
                               page (for mode ``pull``).
                               Default (placeholder — override in your env):
                               34c018ae-d8f8-81ce-8bd1-fbf80defc1e6

    NOTION_TOKEN               REQUIRED for mode ``pull``. Notion
                               integration bearer token with read access
                               to the posture page.

    SWANLAKE_STALE_YELLOW      Days of staleness that triggers the yellow
                               band (mode ``check``). Default: 2

    SWANLAKE_STALE_RED         Days of staleness that triggers the red
                               band (mode ``check``). Default: 7

Exit codes:

    0   Success.
    2   Config missing (e.g. NOTION_TOKEN unset in mode ``pull``).
    3   HTTP error talking to Notion.
    4   Parse error — couldn't find a ``Last verified:`` timestamp or
        couldn't parse the one we found.

Never overwrites ``$SWANLAKE_LAST_RUN`` on any error path. Writes are
atomic (tempfile + os.replace) to avoid partial-write corruption of a
file the status segment reads on every prompt render.

Pairs with:
    tools/status-segment.py  (reads the same SWANLAKE_LAST_RUN file)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path.home()

LAST_RUN = Path(os.environ.get(
    "SWANLAKE_LAST_RUN", str(HOME / ".claude/.last-watchdog-run")
))

# Placeholder — override with your own page id in the env before using
# mode `pull`. Keeping a concrete-shaped default makes copy-paste errors
# obvious (a UUID string is easier to spot-check than an empty string).
DEFAULT_PAGE_ID = "34c018ae-d8f8-81ce-8bd1-fbf80defc1e6"
POSTURE_PAGE_ID = os.environ.get("SWANLAKE_POSTURE_PAGE_ID", DEFAULT_PAGE_ID)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

try:
    YELLOW = int(os.environ.get("SWANLAKE_STALE_YELLOW", "2"))
    RED = int(os.environ.get("SWANLAKE_STALE_RED", "7"))
except ValueError:
    YELLOW, RED = 2, 7


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Uses tempfile + os.replace so the status segment never observes a
    half-written file mid-read. Creates parent directories as needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temp file on the same filesystem as the destination so
    # os.replace is atomic (rename(2) within one mount).
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Clean up the temp file; never leave a stray .tmp behind, and
        # never touch the real file on failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def now_iso() -> str:
    """Current UTC ISO-8601 timestamp with trailing Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Notion fetch
# ---------------------------------------------------------------------------

def fetch_blocks(page_id: str, token: str) -> list[dict[str, Any]]:
    """Fetch top-level block children for ``page_id``.

    Raises ``urllib.error.HTTPError`` on HTTP failures; the caller maps
    that to exit code 3. Returns the raw list of blocks as parsed JSON.
    """
    url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("Notion response missing 'results' array")
    return results


def extract_plain_text(block: dict[str, Any]) -> str:
    """Flatten a block's rich_text array into a plain string.

    Notion callout / paragraph / heading blocks carry their text under
    a type-specific key (``paragraph``, ``callout``, ``heading_1``, ...)
    each with a ``rich_text`` list. We don't care which block type it
    is; we just want the concatenated plain_text.
    """
    btype = block.get("type")
    if not btype:
        return ""
    body = block.get(btype)
    if not isinstance(body, dict):
        return ""
    rich = body.get("rich_text")
    if not isinstance(rich, list):
        return ""
    parts: list[str] = []
    for chunk in rich:
        if isinstance(chunk, dict):
            pt = chunk.get("plain_text")
            if isinstance(pt, str):
                parts.append(pt)
    return "".join(parts)


def find_last_verified(blocks: list[dict[str, Any]]) -> str | None:
    """Scan blocks for the first ``Last verified: <timestamp>`` line.

    Returns the trimmed timestamp substring, or ``None`` if no match.
    We accept any block type — callout, paragraph, heading — because
    the posture page template may evolve. The literal ``Last verified:``
    sentinel is the contract.
    """
    marker = "Last verified:"
    for block in blocks:
        text = extract_plain_text(block)
        if marker in text:
            tail = text.split(marker, 1)[1].strip()
            # Stop at whitespace / end of line — the timestamp should
            # be the next token.
            if not tail:
                continue
            ts = tail.split()[0].strip()
            # Strip trailing punctuation the author may have added.
            ts = ts.rstrip(".,;")
            return ts
    return None


def parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp, accepting both Z and +00:00."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def mode_now() -> int:
    ts = now_iso()
    atomic_write(LAST_RUN, ts + "\n")
    print(f"synced: {ts}")
    return 0


def mode_pull() -> int:
    token = os.environ.get("NOTION_TOKEN", "").strip()
    if not token:
        print("error: NOTION_TOKEN not set", file=sys.stderr)
        return 2
    if not POSTURE_PAGE_ID:
        print("error: SWANLAKE_POSTURE_PAGE_ID not set", file=sys.stderr)
        return 2

    try:
        blocks = fetch_blocks(POSTURE_PAGE_ID, token)
    except urllib.error.HTTPError as e:
        body_snip = ""
        try:
            body_snip = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        print(
            f"error: Notion HTTP {e.code} {e.reason}"
            + (f" — {body_snip}" if body_snip else ""),
            file=sys.stderr,
        )
        return 3
    except urllib.error.URLError as e:
        print(f"error: Notion request failed: {e.reason}", file=sys.stderr)
        return 3
    except (ValueError, json.JSONDecodeError) as e:
        print(f"error: Notion response malformed: {e}", file=sys.stderr)
        return 3

    ts = find_last_verified(blocks)
    if ts is None:
        print(
            "error: no 'Last verified:' marker found on posture page",
            file=sys.stderr,
        )
        return 4

    try:
        parse_iso(ts)
    except ValueError:
        print(f"error: unparseable timestamp: {ts!r}", file=sys.stderr)
        return 4

    atomic_write(LAST_RUN, ts + "\n")
    print(f"synced: {ts}")
    return 0


def mode_check() -> int:
    if not LAST_RUN.exists():
        print("unknown -1")
        return 0
    try:
        raw = LAST_RUN.read_text().strip().splitlines()[0][:32]
        ts = parse_iso(raw)
    except (OSError, IndexError, ValueError):
        print("unknown -1")
        return 0
    delta = datetime.now(timezone.utc) - ts
    days = max(delta.days, 0)
    if days >= RED:
        state = "red"
    elif days >= YELLOW:
        state = "yellow"
    else:
        state = "fresh"
    print(f"{state} {days}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sync-posture.py",
        description=(
            "Sync the Notion Security Posture page's last-verified "
            "timestamp to the local watchdog file that the Swanlake "
            "status segment reads."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Modes:\n"
            "  now    Write current UTC timestamp (default, offline).\n"
            "  pull   Fetch from Notion (requires NOTION_TOKEN).\n"
            "  check  Report staleness state + days.\n"
        ),
    )
    p.add_argument(
        "mode",
        nargs="?",
        default="now",
        choices=("now", "pull", "check"),
        help="Which mode to run (default: now).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "now":
        return mode_now()
    if args.mode == "pull":
        return mode_pull()
    if args.mode == "check":
        return mode_check()
    # argparse should have prevented this, but fail loud if not.
    print(f"error: unknown mode: {args.mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
