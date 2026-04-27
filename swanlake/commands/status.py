"""`swanlake status` -- composite posture across 7 dimensions.

Spec section A9. Each dimension function returns a typed dict and never
raises; the worst severity wins; the exit code is mapped via
exit_codes.{CLEAN, DRIFT, ALARM}. Failures inside a dimension degrade
gracefully to {"status": "unknown", "error": "..."} and contribute
severity 1 (a missing tool is not an attack signal -- spec wording).

Dimensions (in display order):
    reconciler  -> reconciler.status.compute_report()
    canary      -> tools/status_segment.count_today(CANARY_DIR, _canary_hit)
    inject      -> tools/status_segment.count_today(CONTENT_DIR, _content_safety_hit)
    exfil       -> tools/status_segment.count_today(EXFIL_DIR, _exfil_hit)
    closure     -> tools/loop_closure_metric.aggregate_window(today, 7)
    coverage    -> ~/.swanlake/coverage.json mtime + content
    bench       -> ~/.swanlake/last-bench mtime
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from swanlake import _compat
from swanlake import state as _state
from swanlake.exit_codes import ALARM, CLEAN, DRIFT
from swanlake.output import print_json, print_table


# Severity vocabulary used by individual dimensions.
SEVERITY = {
    "clean": 0,
    "ok": 0,
    "fresh": 0,
    "informational": 0,
    "drift": 1,
    "stale": 1,
    "unknown": 1,
    "missing": 2,
    "drift-red": 2,
    "alarm": 2,
}

# Mapping of severity int -> display string + exit code.
SEVERITY_TO_OVERALL = {
    0: ("CLEAN", CLEAN),
    1: ("DRIFT", DRIFT),
    2: ("ALARM", ALARM),
}


def _safe(fn: Callable[[], dict[str, Any]], name: str) -> dict[str, Any]:
    """Run a dimension function; degrade exceptions to status=unknown."""
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001 -- spec mandates degrade-not-raise
        return {
            "name": name,
            "status": "unknown",
            "detail": f"{type(e).__name__}: {e}",
            "error": f"{type(e).__name__}: {e}",
        }
    # Ensure the result carries the dimension name for table rendering.
    result.setdefault("name", name)
    return result


# --- Dimension implementations ---


def _format_age_compact(age_hours: float) -> str:
    """Compact age string: minutes < 1h, hours < 48h, days otherwise."""
    if age_hours < 1:
        minutes = max(int(age_hours * 60), 1)
        return f"{minutes}m"
    if age_hours < 48:
        return f"{age_hours:.0f}h"
    return f"{age_hours / 24:.1f}d"


def _dim_reconciler() -> dict[str, Any]:
    """Reconciler drift across vault / claude_md / notion surfaces.

    Acks (recorded via ``swanlake reconciler ack``) are folded into the
    underlying ``compute_report`` so a remote-only surface (today:
    notion) shows ``ack 5m ago (remote routine)`` instead of the false
    ``missing`` ALARM the local-only sync model produces by default.
    """
    from reconciler import status as recon_status

    report = recon_status.compute_report()
    surfaces = report.get("surfaces", {})
    overall = report.get("overall", "missing")
    detail_parts = []
    for s in ("notion", "claude_md", "vault"):
        d = surfaces.get(s, {}) or {}
        st = d.get("status", "missing")
        via = d.get("synced_via")
        age = d.get("age_hours")
        if st == "missing":
            detail_parts.append(f"{s}: missing")
            continue
        if st == "fresh":
            if via == "ack":
                # Compact "ack Xm ago (remote routine)" surfaces the source.
                if age is None:
                    detail_parts.append(f"{s}: ack (remote routine)")
                else:
                    detail_parts.append(
                        f"{s}: ack {_format_age_compact(age)} ago (remote routine)"
                    )
            else:
                detail_parts.append(f"{s}: fresh")
            continue
        # drift / drift-red rendering keeps the existing compact form.
        if age is None:
            detail_parts.append(f"{s}: {st}")
        elif age < 48:
            detail_parts.append(f"{s}: {age:.0f}h")
        else:
            detail_parts.append(f"{s}: {age / 24:.1f}d")
    # reconciler severity -> our vocabulary
    status_word = {
        "fresh": "clean",
        "drift": "drift",
        "missing": "missing",
        "drift-red": "drift-red",
    }.get(overall, "unknown")
    return {
        "status": status_word,
        "detail": ", ".join(detail_parts),
        "raw_overall": overall,
    }


def _dim_canary() -> dict[str, Any]:
    """Today's canary hits from the status-segment counter."""
    seg = _compat.status_segment_module()
    hits, fires = seg.count_today(seg.CANARY_DIR, seg._canary_hit)
    # Severity semantics (spec A9): any positive hit count -> ALARM. The
    # threat model treats a single canary fire as one too many, so we
    # never demote on low hit counts. The `fires` field is informational
    # only -- we do NOT compare hits-vs-fires (a corrupt log could put
    # them out of sync; the safe default is to surface the alarm and let
    # the operator inspect the underlying logs).
    status = "alarm" if hits > 0 else "clean"
    return {
        "status": status,
        "detail": f"{hits} hits / {fires} fires (24h)",
        "hits": hits,
        "fires": fires,
    }


def _dim_inject() -> dict[str, Any]:
    """Today's content-safety / prompt-injection hits."""
    seg = _compat.status_segment_module()
    hits, fires = seg.count_today(seg.CONTENT_DIR, seg._content_safety_hit)
    # Severity semantics (spec A9): any positive hit count -> ALARM. One
    # detected injection attempt is treated as a confirmed event; we do
    # not require corroboration via the `fires` count because the threat
    # model already accepts the false-positive cost in exchange for never
    # silently swallowing a real hit.
    status = "alarm" if hits > 0 else "clean"
    return {
        "status": status,
        "detail": f"{hits} hits / {fires} fires (24h)",
        "hits": hits,
        "fires": fires,
    }


def _dim_exfil() -> dict[str, Any]:
    """Today's exfil-monitor hits."""
    seg = _compat.status_segment_module()
    hits, fires = seg.count_today(seg.EXFIL_DIR, seg._exfil_hit)
    # Severity semantics (spec A9): any positive hit count -> ALARM. The
    # exfil monitor's false-positive rate is intentionally tuned low
    # enough that a single hit warrants operator attention; a corrupt
    # log file with hits>0 and fires==0 surfaces as ALARM rather than
    # being silently demoted (fail-loud, not fail-soft).
    status = "alarm" if hits > 0 else "clean"
    return {
        "status": status,
        "detail": f"{hits} hits / {fires} fires (24h)",
        "hits": hits,
        "fires": fires,
    }


def _dim_closure() -> dict[str, Any]:
    """7-day rolling closure ratio (artifacts produced / events caught)."""
    lcm = _compat.loop_closure_metric_module()
    today = datetime.now(timezone.utc).date()
    summary = lcm.aggregate_window(today, 7)
    ratio = float(summary.get("ratio") or 0.0)
    events = int(summary.get("total_events") or 0)
    if events < 3:
        # Same threshold the underlying status-flag uses: too little
        # signal to draw a conclusion. Treat as ok with informational
        # detail so the operator sees the count without the row going
        # red.
        return {
            "status": "ok",
            "detail": f"{ratio:.2f} ratio (7d window, {events} events -- low signal)",
            "ratio": ratio,
            "events": events,
        }
    if ratio < 0.30:
        status = "drift"
    else:
        status = "ok"
    return {
        "status": status,
        "detail": f"{ratio:.2f} ratio (7d window)",
        "ratio": ratio,
        "events": events,
    }


def _coverage_path() -> Path:
    return _state.state_path("coverage.json")


def _dim_coverage() -> dict[str, Any]:
    """Inventory-of-inventories age + completeness."""
    p = _coverage_path()
    if not p.exists():
        return {
            "status": "missing",
            "detail": "no coverage.json yet (run swanlake init)",
        }
    age_days = (datetime.now(timezone.utc).timestamp() - p.stat().st_mtime) / 86400
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return {
            "status": "unknown",
            "detail": f"coverage.json unreadable: {type(e).__name__}",
            "error": str(e),
        }
    surfaces = data.get("surfaces") or {}
    n_surfaces = len(surfaces) if isinstance(surfaces, dict) else 0
    if age_days > 7:
        status = "stale"
        detail = f"{age_days:.0f}d since last verify ({n_surfaces} surfaces tracked)"
    else:
        status = "ok"
        detail = f"{age_days:.0f}d old ({n_surfaces} surfaces tracked)"
    return {
        "status": status,
        "detail": detail,
        "age_days": round(age_days, 2),
        "surfaces": n_surfaces,
    }


def _bench_path() -> Path:
    return _state.state_path("last-bench")


def _dim_bench() -> dict[str, Any]:
    """Mtime of the last successful bench run."""
    p = _bench_path()
    if not p.exists():
        return {
            "status": "informational",
            "detail": "no ~/.swanlake/last-bench yet",
        }
    age_days = (datetime.now(timezone.utc).timestamp() - p.stat().st_mtime) / 86400
    if age_days > 30:
        status = "drift"
    else:
        status = "ok"
    return {
        "status": status,
        "detail": f"{age_days:.0f}d since last quick run",
        "age_days": round(age_days, 2),
    }


# --- Composition ---


DIMENSIONS = (
    ("reconciler", _dim_reconciler),
    ("canary", _dim_canary),
    ("inject", _dim_inject),
    ("exfil", _dim_exfil),
    ("closure", _dim_closure),
    ("coverage", _dim_coverage),
    ("bench", _dim_bench),
)


def compute() -> dict[str, Any]:
    """Run every dimension and aggregate into a single report dict."""
    rows = [_safe(fn, name) for name, fn in DIMENSIONS]
    severities = [SEVERITY.get(r.get("status", "unknown"), 1) for r in rows]
    worst = max(severities) if severities else 0
    overall_word, _exit = SEVERITY_TO_OVERALL[worst]
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "dimensions": rows,
        "overall": overall_word,
        "exit_code": _exit,
    }


def run(args) -> int:
    """CLI entry. `args` is the argparse Namespace from swanlake.cli."""
    report = compute()
    if getattr(args, "json", False):
        print_json(report, quiet=getattr(args, "quiet", False))
    else:
        quiet = getattr(args, "quiet", False)
        if not quiet:
            print(f"swanlake status -- {report['ts']}")
            print()
        print_table(
            ({"dimension": d["name"], "status": d["status"], "detail": d.get("detail", "")}
             for d in report["dimensions"]),
            columns=("dimension", "status", "detail"),
            quiet=quiet,
        )
        if not quiet:
            print()
            print(f"overall: {report['overall']}  [exit {report['exit_code']}]")
    return report["exit_code"]
