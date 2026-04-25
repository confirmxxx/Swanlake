"""Findings sink — Phase 1 stub.

Phase 1 writes accepted findings to state/findings.jsonl. Phase 2/3
will replace this with real GitHub-issue creation against the Swanlake
repo. The boundary is intentional: the supervisor depends only on the
abstract `FindingsSink` interface, so swapping in a `GitHubIssuesSink`
later changes nothing else.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol


class FindingsSink(Protocol):
    def write(self, finding: dict) -> str:
        """Persist a finding. Returns a stable finding-id string."""
        ...

    def quarantine(self, finding: dict, reason: str) -> str:
        """Persist a quarantined finding with the rejection reason.
        Returns the quarantine-row id."""
        ...

    def invalid(self, finding: dict, reason: str) -> None:
        """Log a schema-rejected finding for operator review."""
        ...


class JsonlFindingsSink:
    """Phase 1 sink. Append-only JSONL files under state/."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.findings_path = self.state_dir / "findings.jsonl"
        self.quarantine_path = self.state_dir / "quarantine.jsonl"
        self.invalid_path = self.state_dir / "invalid.jsonl"

    @staticmethod
    def _finding_id(finding: dict) -> str:
        # Deterministic id over persona + produced_utc + title; short for
        # operator readability. SHA-256 truncated to 12 chars is plenty
        # for Phase 1's expected volume (<100 findings).
        h = hashlib.sha256()
        h.update(finding.get("persona", "").encode("utf-8"))
        h.update(b"\x00")
        h.update(finding.get("produced_utc", "").encode("utf-8"))
        h.update(b"\x00")
        h.update(finding.get("title", "").encode("utf-8"))
        return "wc-" + h.hexdigest()[:12]

    def write(self, finding: dict) -> str:
        fid = self._finding_id(finding)
        row = {
            "id": fid,
            "filed_utc": _now_utc_iso(),
            "finding": finding,
            "closure_artifact": None,  # operator fills via closure_rate.close()
            "closure_recorded_utc": None,
        }
        with self.findings_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        return fid

    def quarantine(self, finding: dict, reason: str) -> str:
        fid = self._finding_id(finding) + "-q"
        row = {
            "id": fid,
            "quarantined_utc": _now_utc_iso(),
            "reason": reason,
            # Persona-attributable identity only; we deliberately do not
            # round-trip the offending strings into another file (the whole
            # point of canary quarantine is to *not* propagate the literal).
            "persona": finding.get("persona", "<unknown>") if isinstance(finding, dict) else "<non-dict>",
            "title": finding.get("title", "<unknown>") if isinstance(finding, dict) else "<non-dict>",
        }
        with self.quarantine_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        return fid

    def invalid(self, finding: dict, reason: str) -> None:
        row = {
            "logged_utc": _now_utc_iso(),
            "reason": reason,
            "persona": finding.get("persona", "<unknown>") if isinstance(finding, dict) else "<non-dict>",
        }
        with self.invalid_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def _now_utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
