"""Status engine — drift detection across all surface classes.

Reads ~/.config/swanlake-reconciler/last-sync.json (per-surface ISO
timestamps written by sync engines). Classifies each surface by age
vs current time. Severity ordering: fresh < drift < missing < drift-red.
`missing` is worse than `drift` (never synced is more concerning than
stale-but-known); `drift-red` (stale > 7d) is worst.

Also reads ~/.swanlake/reconciler-acks.jsonl (per-surface operator
acks) for surfaces that are synced by remote routines outside the
reconciler's reach (notion, today). An ack is folded into the
freshness calculation only when it is fresher than the local sync
timestamp; the most recent of (sync_ts, ack_ts) wins. Acks age out on
the same windows as syncs, so a forgotten ack does NOT permanently
mute the alarm.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reconciler import acks as _acks

# Default state path; overridable via state_path arg in tests.
STATE_PATH = Path.home() / '.config' / 'swanlake-reconciler' / 'last-sync.json'

SURFACES = ('claude_md', 'notion', 'vault')

# Window thresholds — tune here, single source of truth.
FRESH_WINDOW = timedelta(hours=24)
DRIFT_WINDOW = timedelta(days=7)

# Severity ordering (higher = worse). `missing` beats `drift` because never
# synced is structurally more concerning than stale-but-recorded.
_SEVERITY = {
    'fresh': 0,
    'drift': 1,
    'missing': 2,
    'drift-red': 3,
}


def _classify(synced_at: datetime | None, now: datetime) -> str:
    if synced_at is None:
        return 'missing'
    age = now - synced_at
    if age < FRESH_WINDOW:
        return 'fresh'
    if age < DRIFT_WINDOW:
        return 'drift'
    return 'drift-red'


def _read_state(state_path: Path) -> dict:
    """Read sync-state JSON. Any failure → empty dict (treat as all-missing)."""
    try:
        return json.loads(state_path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse ISO timestamp; treat unparseable as missing."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def compute_report(
    state_path: Path = STATE_PATH,
    acks_state_root: Path | None = None,
) -> dict:
    """Return per-surface freshness folded with operator acks.

    Each surface entry carries:
      - ``status``: fresh/drift/missing/drift-red (severity bucket)
      - ``last_sync_utc``: ISO timestamp of the most recent local sync (or None)
      - ``last_ack_utc``: ISO timestamp of the most recent operator ack (or None)
      - ``synced_via``: ``"sync"`` or ``"ack"`` — which signal the freshness
        bucket above is computed against. ``None`` when neither exists.
      - ``age_hours``: age of whichever signal won, in hours (or None)

    Acks are read from ``~/.swanlake/reconciler-acks.jsonl`` (overridable
    via ``acks_state_root`` for tests). The fresher of (sync_ts, ack_ts)
    wins. Acks decay on the same FRESH/DRIFT windows as syncs, so a
    forgotten ack still goes red instead of permanently muting the alarm.
    """
    now = datetime.now(timezone.utc)
    raw = _read_state(state_path)
    ack_map = _acks.latest_acks(state_root=acks_state_root)
    surfaces: dict[str, dict] = {}
    for s in SURFACES:
        synced = _parse_timestamp(raw.get(s))
        ack = ack_map.get(s)
        ack_ts = ack.synced_at if ack is not None else None

        # Fresher of (sync, ack) wins. None values lose to anything real.
        if synced is None and ack_ts is None:
            winning_ts: datetime | None = None
            via: str | None = None
        elif synced is None:
            winning_ts = ack_ts
            via = 'ack'
        elif ack_ts is None:
            winning_ts = synced
            via = 'sync'
        elif ack_ts >= synced:
            winning_ts = ack_ts
            via = 'ack'
        else:
            winning_ts = synced
            via = 'sync'

        st = _classify(winning_ts, now)
        surfaces[s] = {
            'status': st,
            'last_sync_utc': synced.isoformat() if synced else None,
            'last_ack_utc': ack_ts.isoformat() if ack_ts else None,
            'synced_via': via,
            'age_hours': (now - winning_ts).total_seconds() / 3600 if winning_ts else None,
        }
    overall = max(
        (surfaces[s]['status'] for s in SURFACES),
        key=lambda x: _SEVERITY[x],
    )
    return {'surfaces': surfaces, 'overall': overall}


def run_status() -> int:
    """CLI entry: print human-readable report. Exit 0=fresh, 1=drift, 2=missing/drift-red."""
    report = compute_report()
    print(f"swanlake-reconciler status — overall: {report['overall']}")
    print(f"{'surface':<12} {'status':<12} {'via':<6} {'last signal (UTC)':<32} {'age':<8}")
    for s in SURFACES:
        d = report['surfaces'][s]
        via = d.get('synced_via') or '-'
        # Show whichever timestamp won the freshness calculation.
        if via == 'ack':
            last = d['last_ack_utc'] or '-'
        else:
            last = d['last_sync_utc'] or '-'
        age = f'{d["age_hours"]:.1f}h' if d['age_hours'] is not None else '-'
        print(f'{s:<12} {d["status"]:<12} {via:<6} {last:<32} {age:<8}')
    return {'fresh': 0, 'drift': 1, 'missing': 2, 'drift-red': 2}[report['overall']]


def write_sync_timestamp(surface: str, when: datetime | None = None,
                         state_path: Path = STATE_PATH) -> None:
    """Atomically record a successful sync.

    Concurrency-safe: holds fcntl.flock on a sidecar lockfile during the
    read-modify-write. Crash-safe: writes to a temp file in the same
    directory then os.replace() (atomic on POSIX).
    """
    when = when or datetime.now(timezone.utc)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(state_path.suffix + '.lock')

    # fcntl.flock requires an open fd. Use the lock file separately so the
    # state file write can use os.replace() atomically.
    with open(lock_path, 'w') as lock_fp:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
        try:
            raw = _read_state(state_path)
            raw[surface] = when.isoformat()
            # Write to temp file in the same dir, then atomic rename.
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=state_path.name + '.',
                suffix='.tmp',
                dir=str(state_path.parent),
            )
            try:
                with os.fdopen(tmp_fd, 'w') as f:
                    json.dump(raw, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, state_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
