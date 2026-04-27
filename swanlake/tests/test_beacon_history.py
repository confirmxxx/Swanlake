"""Tests for beacon-deploy-history.jsonl writer."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import state as _state
from swanlake.commands.beacon import _history


class HistoryAppendTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()

    def test_append_single_record(self):
        _history.append({
            "op": "deploy",
            "surface": "cms-test",
            "type": "claude-md",
            "method": "local-write",
            "outcome": "deployed",
            "backup_path": "/tmp/x.bak",
        })
        records = _history.read_all()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["op"], "deploy")
        self.assertEqual(rec["outcome"], "deployed")
        self.assertIn("ts", rec)
        self.assertIn("swanlake_version", rec)
        self.assertEqual(rec["pid"], records[0]["pid"])

    def test_append_does_not_raise_on_unwritable_root(self):
        _state.set_state_root(Path("/proc/this/will/never/work"))
        # Must not raise.
        _history.append({"op": "deploy", "outcome": "deployed"})

    def test_outcomes_round_trip(self):
        outcomes = (
            "deployed",
            "checklist-printed",
            "aborted-clean-tree",
            "aborted-no-confirm",
            "aborted-replace-conflict",
            "dry-run",
            "skipped-by-optout",
            "error",
        )
        for outcome in outcomes:
            _history.append({"op": "deploy", "outcome": outcome})
        records = _history.read_all()
        seen = {r["outcome"] for r in records}
        for outcome in outcomes:
            self.assertIn(outcome, seen)


if __name__ == "__main__":
    unittest.main()
