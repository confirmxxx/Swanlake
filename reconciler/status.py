"""Status engine — drift detection across all surface classes.

Reads ~/.config/swanlake-reconciler/last-sync.json (per-surface ISO
timestamps written by sync engines). Classifies each surface by age
vs current time:
  fresh     — synced within 24h
  drift     — synced 24h to 7d ago (status segment shows yellow)
  drift-red — synced > 7d ago (status segment shows red)
  missing   — no sync timestamp recorded
Plus an `overall` field aggregated from per-surface (worst wins).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Default state path; overridable in tests.
STATE_PATH = Path.home() / '.config' / 'swanlake-reconciler' / 'last-sync.json'

SURFACES = ('claude_md', 'notion', 'vault')


def _classify(synced_at: datetime | None, now: datetime) -> str:
    if synced_at is None:
        return 'missing'
    age = now - synced_at
    if age < timedelta(hours=24):
        return 'fresh'
    if age < timedelta(days=7):
        return 'drift'
    return 'drift-red'


def compute_report(state_path: Path = STATE_PATH) -> dict:
    """Return {'surfaces': {name: {'status', 'last_sync_utc', 'age_hours'}}, 'overall': str}."""
    now = datetime.now(timezone.utc)
    try:
        raw = json.loads(state_path.read_text())
    except FileNotFoundError:
        raw = {}
    surfaces: dict[str, dict] = {}
    for s in SURFACES:
        ts_str = raw.get(s)
        synced = datetime.fromisoformat(ts_str) if ts_str else None
        st = _classify(synced, now)
        surfaces[s] = {
            'status': st,
            'last_sync_utc': synced.isoformat() if synced else None,
            'age_hours': (now - synced).total_seconds() / 3600 if synced else None,
        }
    severity = {'fresh': 0, 'drift': 1, 'drift-red': 2, 'missing': 1}
    overall = max((surfaces[s]['status'] for s in SURFACES), key=lambda x: severity[x])
    return {'surfaces': surfaces, 'overall': overall}


def run_status() -> int:
    """CLI entry: print human-readable report, exit 0 if fresh, 1 if drift, 2 if red."""
    report = compute_report()
    print(f"swanlake-reconciler status — overall: {report['overall']}")
    print(f"{'surface':<12} {'status':<12} {'last sync (UTC)':<32} {'age':<8}")
    for s in SURFACES:
        d = report['surfaces'][s]
        last = d['last_sync_utc'] or '-'
        age = f'{d["age_hours"]:.1f}h' if d['age_hours'] is not None else '-'
        print(f'{s:<12} {d["status"]:<12} {last:<32} {age:<8}')
    return {'fresh': 0, 'drift': 1, 'missing': 1, 'drift-red': 2}[report['overall']]


def write_sync_timestamp(surface: str, when: datetime | None = None,
                         state_path: Path = STATE_PATH) -> None:
    """Called by sync engines to record a successful sync."""
    when = when or datetime.now(timezone.utc)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(state_path.read_text())
    except FileNotFoundError:
        raw = {}
    raw[surface] = when.isoformat()
    state_path.write_text(json.dumps(raw, indent=2))
