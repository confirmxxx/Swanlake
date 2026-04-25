"""Persona-output schema validation.

Phase 1 schema (v1) — see SPEC.md "Persona output schema". Stdlib only.
The validator returns a (ok, error) tuple; callers route invalid findings
to the supervisor's `findings_invalid` counter and never to the sink.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# Field byte caps — see SPEC.md.
_TITLE_MAX = 120
_SUMMARY_MAX = 2000
_REPRO_MAX = 4000

_ALLOWED_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_ALLOWED_FIXTURES = {"mock-notion", "mock-github", "mock-vercel"}
_ALLOWED_CLOSURES = {"hook-rule", "deny-entry", "fixture", "doc-note", "none"}
_ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}

_REQUIRED_KEYS = (
    "persona",
    "produced_utc",
    "atlas_ttp",
    "severity",
    "title",
    "summary",
    "reproduction",
    "fixture_hits",
    "suggested_closure",
    "schema_version",
)


def _err(msg: str) -> tuple[bool, str]:
    return False, msg


def validate(
    finding: Any,
    *,
    expected_persona: str,
    atlas_ttps: set[str],
) -> tuple[bool, str]:
    """Return (ok, error). `expected_persona` is the dispatching persona
    name; the finding's `persona` field must match. `atlas_ttps` is the
    set of valid TTP IDs loaded from atlas-taxonomy.yaml."""

    if not isinstance(finding, dict):
        return _err(f"finding is not a dict (got {type(finding).__name__})")

    missing = [k for k in _REQUIRED_KEYS if k not in finding]
    if missing:
        return _err(f"missing required keys: {sorted(missing)}")

    extra = sorted(set(finding.keys()) - set(_REQUIRED_KEYS))
    if extra:
        return _err(f"unknown keys present: {extra}")

    if finding["schema_version"] != 1:
        return _err(f"schema_version must be 1, got {finding['schema_version']!r}")

    if finding["persona"] != expected_persona:
        return _err(
            f"persona mismatch: dispatched {expected_persona!r}, "
            f"finding claims {finding['persona']!r}"
        )

    ts = finding["produced_utc"]
    if not isinstance(ts, str):
        return _err("produced_utc must be a string")
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError as exc:
        return _err(f"produced_utc not ISO 8601 ({exc})")
    if parsed.tzinfo is None:
        return _err("produced_utc missing timezone offset")
    if parsed.utcoffset().total_seconds() != 0:
        return _err("produced_utc must be UTC (+00:00)")

    ttps = finding["atlas_ttp"]
    if not isinstance(ttps, list) or not ttps:
        return _err("atlas_ttp must be a non-empty list")
    for t in ttps:
        if not isinstance(t, str):
            return _err(f"atlas_ttp entry not a string: {t!r}")
        if t not in atlas_ttps:
            return _err(f"atlas_ttp {t!r} not in taxonomy")

    if finding["severity"] not in _ALLOWED_SEVERITIES:
        return _err(
            f"severity {finding['severity']!r} not in "
            f"{sorted(_ALLOWED_SEVERITIES)}"
        )

    for field, cap in (("title", _TITLE_MAX), ("summary", _SUMMARY_MAX), ("reproduction", _REPRO_MAX)):
        v = finding[field]
        if not isinstance(v, str):
            return _err(f"{field} must be a string")
        if len(v.encode("utf-8")) > cap:
            return _err(f"{field} exceeds {cap}-byte cap")

    fh = finding["fixture_hits"]
    if not isinstance(fh, list):
        return _err("fixture_hits must be a list")
    for entry in fh:
        if not isinstance(entry, dict):
            return _err(f"fixture_hits entry not a dict: {entry!r}")
        for k in ("service", "path", "method"):
            if k not in entry:
                return _err(f"fixture_hits entry missing {k!r}")
        if entry["service"] not in _ALLOWED_FIXTURES:
            return _err(f"fixture_hits service {entry['service']!r} unknown")
        if entry["method"] not in _ALLOWED_METHODS:
            return _err(f"fixture_hits method {entry['method']!r} unknown")
        if not isinstance(entry["path"], str) or not entry["path"].startswith("/"):
            return _err(f"fixture_hits path must be absolute: {entry['path']!r}")

    if finding["suggested_closure"] not in _ALLOWED_CLOSURES:
        return _err(
            f"suggested_closure {finding['suggested_closure']!r} not in "
            f"{sorted(_ALLOWED_CLOSURES)}"
        )

    return True, ""


def load_taxonomy(path) -> set[str]:
    """Tiny YAML loader — Phase 1 atlas-taxonomy.yaml is one map under
    a single top-level `ttps` key. We avoid a PyYAML dependency by
    parsing the file with a hand-rolled reader; the format is simple
    enough that a real YAML parser would be overkill."""
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8")
    out: set[str] = set()
    in_ttps = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not in_ttps:
            if stripped.startswith("ttps:"):
                in_ttps = True
            continue
        if not (line.startswith(" ") or line.startswith("\t")):
            in_ttps = False
            continue
        if ":" not in stripped:
            continue
        key, _ = stripped.split(":", 1)
        out.add(key.strip())
    return out
