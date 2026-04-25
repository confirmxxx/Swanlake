"""Closure-rate counter + CLI.

The Phase-1 anti-theater gate. The mechanical kill criterion:

    closure ratio < 30% over the last 4 weeks → kill the project.

`record_run` writes one JSONL row to state/closure-rate.jsonl per
supervisor pass. `report` summarizes the rolling window. `close` lets
the operator mark a finding as closed-to-artifact (which is the
*denominator* this whole thing exists to make honest). `kill-check`
exits non-zero when the 4-week ratio is below 30%.

Closure window: a finding counts as "closed to artifact" only if it
was filed at least 14 days ago AND has a non-empty `closure_artifact`
field. The 14-day grace prevents declaring victory on findings filed
this week — the artifact must outlast a review cycle.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_GRACE_DAYS = 14
_KILL_WINDOW_DAYS = 28
_KILL_THRESHOLD = 0.30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class ClosureRateCounter:
    """Reads findings.jsonl, computes ratio, appends rows to
    closure-rate.jsonl. State files live under state_dir."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.findings_path = self.state_dir / "findings.jsonl"
        self.closure_path = self.state_dir / "closure-rate.jsonl"

    def _read_findings(self) -> list[dict]:
        if not self.findings_path.exists():
            return []
        rows: list[dict] = []
        with self.findings_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    # Skip corrupt rows; a real run would log this. Phase 1
                    # treats findings.jsonl as append-only, so corruption
                    # implies disk error and is left for operator review.
                    continue
        return rows

    def compute(self, now: datetime | None = None) -> tuple[int, int, float]:
        """Return (filed, closed_to_artifact, ratio).

        - filed: total findings persisted to findings.jsonl
        - closed_to_artifact: findings filed >= 14 days ago AND with a
          non-empty closure_artifact field
        - ratio: closed_to_artifact / filed (0.0 if filed == 0)
        """
        now = now or _utcnow()
        cutoff = now - timedelta(days=_GRACE_DAYS)
        rows = self._read_findings()
        filed = len(rows)
        closed = 0
        for r in rows:
            artifact = r.get("closure_artifact")
            if not artifact:
                continue
            ts = r.get("filed_utc")
            if not ts:
                continue
            try:
                filed_at = _parse_iso(ts)
            except ValueError:
                continue
            if filed_at <= cutoff:
                closed += 1
        ratio = (closed / filed) if filed else 0.0
        return filed, closed, ratio

    def record_run(self, now: datetime | None = None) -> dict:
        now = now or _utcnow()
        filed, closed, ratio = self.compute(now=now)
        row = {
            "date": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "findings_filed": filed,
            "findings_closed_to_artifact": closed,
            "ratio": round(ratio, 4),
        }
        with self.closure_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def report(self, window_days: int = 30, now: datetime | None = None) -> dict:
        """Return summary for the last `window_days`."""
        now = now or _utcnow()
        cutoff = now - timedelta(days=window_days)
        if not self.closure_path.exists():
            return {"window_days": window_days, "rows": 0, "latest_ratio": 0.0}
        rows: list[dict] = []
        with self.closure_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    when = _parse_iso(row.get("date", ""))
                except (ValueError, TypeError):
                    continue
                if when >= cutoff:
                    rows.append(row)
        latest = rows[-1]["ratio"] if rows else 0.0
        return {
            "window_days": window_days,
            "rows": len(rows),
            "latest_ratio": latest,
            "latest_filed": rows[-1]["findings_filed"] if rows else 0,
            "latest_closed": rows[-1]["findings_closed_to_artifact"] if rows else 0,
        }

    def close(self, finding_id: str, artifact_ref: str, now: datetime | None = None) -> bool:
        """Stamp a finding row with closure_artifact + closure_recorded_utc.
        Returns True if the row was found and updated, False otherwise.

        Atomic via tempfile + os.replace — partial corruption on crash
        is impossible."""
        import os
        import tempfile

        now = now or _utcnow()
        if not self.findings_path.exists():
            return False

        rows = self._read_findings()
        updated = False
        for r in rows:
            if r.get("id") == finding_id:
                r["closure_artifact"] = artifact_ref
                r["closure_recorded_utc"] = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
                updated = True
                break

        if not updated:
            return False

        # Atomic rewrite.
        fd, tmp_path = tempfile.mkstemp(
            prefix="findings.", suffix=".tmp", dir=str(self.state_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, sort_keys=True) + "\n")
            os.replace(tmp_path, self.findings_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return True

    def kill_check(self, now: datetime | None = None) -> tuple[bool, dict]:
        """Return (alive, info). alive=False means closure ratio is below
        the kill threshold over the kill window. The CLI exits 1 when
        alive is False."""
        now = now or _utcnow()
        cutoff = now - timedelta(days=_KILL_WINDOW_DAYS)
        if not self.closure_path.exists():
            return True, {
                "alive": True,
                "reason": "no closure-rate rows yet — too early to evaluate",
                "window_days": _KILL_WINDOW_DAYS,
            }
        rows: list[dict] = []
        with self.closure_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    when = _parse_iso(row.get("date", ""))
                except (ValueError, TypeError):
                    continue
                if when >= cutoff:
                    rows.append(row)
        if not rows:
            return True, {
                "alive": True,
                "reason": f"no closure-rate rows in last {_KILL_WINDOW_DAYS} days",
                "window_days": _KILL_WINDOW_DAYS,
            }
        # Use the most-recent row's ratio. The mechanical criterion is
        # "after 4 weeks", so we evaluate the rolling window's latest
        # state — not an average that smooths over the recent shape.
        latest = rows[-1]
        alive = latest["ratio"] >= _KILL_THRESHOLD
        return alive, {
            "alive": alive,
            "ratio": latest["ratio"],
            "threshold": _KILL_THRESHOLD,
            "window_days": _KILL_WINDOW_DAYS,
            "filed": latest["findings_filed"],
            "closed": latest["findings_closed_to_artifact"],
        }


# Make module runnable as `python3 -m supervisor.closure_rate ...` after
# the orchestrator's sys.path shim runs, OR directly:
#     python3 experiments/white-cells/supervisor/closure_rate.py report
def _default_state_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "state"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="white-cells-closure-rate")
    parser.add_argument("--state-dir", default=str(_default_state_dir()))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_report = sub.add_parser("report", help="summarize the rolling window")
    p_report.add_argument("--window", type=int, default=30)

    p_close = sub.add_parser("close", help="mark a finding closed to an artifact")
    p_close.add_argument("finding_id")
    p_close.add_argument("artifact_ref")

    sub.add_parser("kill-check", help="exit 1 if closure ratio below threshold")

    args = parser.parse_args(argv)
    counter = ClosureRateCounter(Path(args.state_dir))

    if args.cmd == "report":
        out = counter.report(window_days=args.window)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    if args.cmd == "close":
        ok = counter.close(args.finding_id, args.artifact_ref)
        if not ok:
            print(f"finding-id not found: {args.finding_id}", file=sys.stderr)
            return 1
        print(f"closed: {args.finding_id} -> {args.artifact_ref}")
        return 0
    if args.cmd == "kill-check":
        alive, info = counter.kill_check()
        print(json.dumps(info, indent=2, sort_keys=True))
        return 0 if alive else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
