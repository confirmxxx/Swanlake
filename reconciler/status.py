"""Status engine — drift detection across all surface classes.

Reads ~/.swanlake/last-sync.json (per-surface ISO timestamps written
by sync engines). Classifies each surface by age vs current time.
Severity ordering: fresh < drift < missing < drift-red.
`missing` is worse than `drift` (never synced is more concerning than
stale-but-known); `drift-red` (stale > 7d) is worst.

Also reads ~/.swanlake/reconciler-acks.jsonl (per-surface operator
acks) for surfaces that are synced by remote routines outside the
reconciler's reach (notion, today). An ack is folded into the
freshness calculation only when it is fresher than the local sync
timestamp; the most recent of (sync_ts, ack_ts) wins. Acks age out on
the same windows as syncs, so a forgotten ack does NOT permanently
mute the alarm.

Path migration (v0.4.2)
-----------------------
``STATE_PATH`` was historically rooted at the legacy XDG location
``~/.config/swanlake-reconciler/last-sync.json``. The unified state
root (spec A3 / A11) is ``~/.swanlake/`` and the ack JSONL already
lives there. Leaving ``STATE_PATH`` on the legacy path made
``state_path.parent`` resolve to ``~/.config/swanlake-reconciler/``,
which is NOT where ``reconciler.acks`` reads acks from -- so acks
recorded via ``swanlake reconciler ack`` were silently invisible to
``swanlake status``.

This module now defaults ``STATE_PATH`` to the new location and runs
a one-shot best-effort migration on first read: if the new file does
not exist but the legacy one does, the legacy contents are copied
forward. The legacy file is NOT deleted -- operators may have other
tooling reading it, and the reconciler is not authoritative over
files outside its own state root.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reconciler import acks as _acks

# Unified state root (spec A3 / A11). ``STATE_PATH`` is the active path.
# The legacy XDG path is consulted only by the one-shot migration helper
# below; once a new file exists there, the legacy file is never read again.
STATE_PATH = Path.home() / '.swanlake' / 'last-sync.json'
_LEGACY_STATE_PATH = Path.home() / '.config' / 'swanlake-reconciler' / 'last-sync.json'

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


def _atomic_write_state(path: Path, text: str) -> None:
    """Atomic write helper used by the legacy migration path.

    Mirrors ``write_sync_timestamp``'s tempfile-in-same-dir + os.replace
    pattern so a half-migrated file never lands on disk. Pulled out as
    a free function (instead of reusing ``write_sync_timestamp``)
    because the migration writes raw JSON bytes verbatim -- adding a
    re-encode step would be a chance to corrupt the legacy contents.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + '.', suffix='.tmp', dir=str(path.parent),
    )
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _maybe_migrate_legacy_state(state_path: Path) -> None:
    """One-shot best-effort copy of the legacy state file forward.

    Runs only when:
      * ``state_path`` is the default ``STATE_PATH`` (we never migrate
        an explicit override -- tests pass tempdirs and would not want
        the operator's real legacy file leaking in)
      * the new location does not exist yet
      * the legacy location exists and is readable

    The legacy file is left in place. Any error during migration is
    swallowed silently: the read path falls back to "all-missing"
    semantics, which is the safer behaviour than crashing the status
    command on a flaky filesystem.
    """
    if state_path != STATE_PATH:
        return
    if state_path.exists():
        return
    if not _LEGACY_STATE_PATH.exists():
        return
    try:
        legacy_text = _LEGACY_STATE_PATH.read_text(encoding='utf-8')
    except OSError:
        return
    try:
        # Validate it parses before we copy so we don't migrate junk.
        json.loads(legacy_text)
    except json.JSONDecodeError:
        return
    try:
        _atomic_write_state(state_path, legacy_text)
    except OSError:
        # ENOSPC, EACCES, etc. -- migration is best-effort. Leave the
        # legacy file alone; next status call will retry.
        return


def _read_state(state_path: Path) -> dict:
    """Read sync-state JSON. Any failure → empty dict (treat as all-missing).

    Triggers a one-shot migration from the legacy XDG path on first
    read of the default state path. See ``_maybe_migrate_legacy_state``.
    """
    _maybe_migrate_legacy_state(state_path)
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
    # Default acks_state_root to state_path.parent so the path remains
    # internally consistent: in production STATE_PATH lives in
    # ``~/.swanlake/`` so .parent matches ``reconciler.acks`` defaults
    # (post-v0.4.2 migration). In tests, a tmp state_path is paired with
    # a tmp acks dir of the same parent, which is the test-isolation
    # invariant the v0.4.1 fix introduced. Pre-v0.4.2 the parent
    # resolved to the LEGACY XDG dir, which silently broke production
    # acks (see module docstring); the migration above is what makes
    # this default safe.
    if acks_state_root is None:
        acks_state_root = state_path.parent
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
