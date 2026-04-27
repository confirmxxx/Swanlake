"""beacon-deploy-history.jsonl writer (spec section 4).

Append-only log of every `swanlake beacon` invocation that touches a
surface or generates a checklist. Schema:

    {
      "ts": "<ISO-UTC>",
      "op": "deploy|checklist|sweep",
      "surface": "<id-or-null>",
      "type": "<surface-type-or-null>",
      "method": "local-write|remote-checklist|pr-checklist|null",
      "outcome": "deployed|checklist-printed|aborted-clean-tree|aborted-no-confirm|...",
      "backup_path": "<path-or-null>",
      "post_git_status": "<short-or-null>",
      "summary": {...}|null,
      "swanlake_version": "0.3.0-dev",
      "pid": 12345
    }

Same atomic-append pattern as audit.jsonl: fcntl.flock + single
sub-PIPE_BUF write. Never raises -- a broken history log must not break
the CLI.
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from swanlake import __version__
from swanlake.state import ensure_state_root, state_path

HISTORY_FILENAME = "beacon-deploy-history.jsonl"
ROTATED_FILENAME = "beacon-deploy-history.jsonl.1"
ROTATION_BYTES = 10 * 1024 * 1024


def _maybe_rotate(history_file: Path) -> None:
    try:
        if (
            history_file.exists()
            and history_file.stat().st_size >= ROTATION_BYTES
        ):
            rotated = history_file.parent / ROTATED_FILENAME
            os.replace(history_file, rotated)
    except OSError:
        # Rotation failures are silent; next append retries.
        pass


def _atomic_append(history_file: Path, line: str) -> None:
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with open(history_file, "ab") as fp:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
            fp.write(line.encode("utf-8"))
            fp.flush()
            try:
                os.fsync(fp.fileno())
            except OSError:
                pass
        finally:
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def append(record: dict[str, Any]) -> None:
    """Append one record to beacon-deploy-history.jsonl. Never raises.

    The caller supplies a partial record; this function fills in `ts`,
    `swanlake_version`, and `pid`. Missing optional fields default to None.
    """
    try:
        ensure_state_root()
        full = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": record.get("op"),
            "surface": record.get("surface"),
            "type": record.get("type"),
            "method": record.get("method"),
            "outcome": record.get("outcome"),
            "backup_path": record.get("backup_path"),
            "post_git_status": record.get("post_git_status"),
            "summary": record.get("summary"),
            "swanlake_version": __version__,
            "pid": os.getpid(),
        }
        history_file = state_path(HISTORY_FILENAME)
        _maybe_rotate(history_file)
        line = json.dumps(full, separators=(",", ":"), sort_keys=True) + "\n"
        _atomic_append(history_file, line)
    except Exception:
        # History is best-effort; never break the CLI.
        pass


def read_all() -> list[dict[str, Any]]:
    """Read every record from history. Returns [] on missing/unreadable."""
    history_file = state_path(HISTORY_FILENAME)
    if not history_file.exists():
        return []
    try:
        text = history_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


__all__ = ["append", "read_all", "HISTORY_FILENAME", "ROTATION_BYTES"]
