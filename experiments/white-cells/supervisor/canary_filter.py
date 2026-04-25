"""Post-filter for persona output.

If a persona's emitted finding contains a real-shaped Defense Beacon
canary literal in its summary or reproduction string, the persona is
treated as compromised (or the fixture sandbox has been poisoned). The
finding is **quarantined**, never reaches the sink, and an alert row
is appended to state/quarantine.jsonl by the caller.

Patterns mirror defense-beacon/.claude/hooks/canary-literal-block.sh
exactly so the two layers agree on what counts as real-shaped. Test
fixtures that look obviously fake (e.g. AKIA_BEACON_TESTFIXTURE...) are
length-bounded out of the AWS pattern.
"""
from __future__ import annotations

import re
from typing import Iterable

# Real-canary patterns. Length-bounded so obviously-fake placeholders
# (e.g. AKIA_BEACON_TESTFIXTURE000000000000 — has 'T','S','I','U' that
# fail [0-9A-F]) are not matched.
_AWS_RE = re.compile(r"AKIA_BEACON_[0-9A-F]{20}")
_GOOGLE_RE = re.compile(r"AIzaSy[A-Za-z0-9_\-]{30,}")
_ATTRIB_RE = re.compile(r"beacon-attrib-[a-z0-9-]+-[A-Za-z0-9]{8}")

_PATTERNS = (
    ("aws", _AWS_RE),
    ("google", _GOOGLE_RE),
    ("attrib", _ATTRIB_RE),
)

# Fields scanned. Other fields are operator-controlled (severity, ttp tags)
# and not free-text, so they cannot smuggle a literal.
_SCANNED_FIELDS = ("title", "summary", "reproduction")


def detect_canaries(finding: dict) -> list[str]:
    """Return the list of canary kinds present (deduplicated). Empty
    list means clean."""
    hits: list[str] = []
    seen: set[str] = set()
    for field in _SCANNED_FIELDS:
        v = finding.get(field, "")
        if not isinstance(v, str):
            continue
        for kind, pat in _PATTERNS:
            if kind in seen:
                continue
            if pat.search(v):
                hits.append(kind)
                seen.add(kind)
    return hits


def is_clean(finding: dict) -> bool:
    return not detect_canaries(finding)


def redacted_kinds(kinds: Iterable[str]) -> str:
    """Format hit kinds for stderr / log output. Never echoes the
    matched literal — only its kind label."""
    return ", ".join(f"REDACTED(canary_kind={k})" for k in kinds)
