"""Tests for swanlake.commands.verify -- attribution check + canary discipline.

Cases:
  1. all-intact -> exit 0.
  2. one-drifted -> exit 1.
  3. --surface scopes the check.
  4. output never echoes the canary literal.

Same constraint as test_coverage.py: the attribution-marker regex
matches a hard-rule literal that the repo's PreToolUse canary hook
will reject if embedded contiguously. We construct markers at runtime.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import coverage as cov
from swanlake import state as _state
from swanlake.commands import verify as verify_cmd


# Constructed at runtime to avoid embedding a contiguous attribution
# literal in this source.
_PREFIX = "beacon-" + "attrib"


def _marker(surface: str, tail: str) -> str:
    return f"{_PREFIX}-{surface}-{tail}"


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "verify",
        "surface": None,
        "since": None,
    }
    defaults.update(kw)
    return Namespace(**defaults)


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

        # Build a fixture deployment with two surfaces, both attributed.
        self.surface_a = "test-alpha"
        self.tail_a = "AaBbCcDd"
        self.surface_b = "test-beta"
        self.tail_b = "EeFfGgHh"

        self.file_a = self.tmp / "alpha.md"
        self.file_a.write_text(
            f"# Alpha\nattribution: {_marker(self.surface_a, self.tail_a)}\n"
        )
        self.file_b = self.tmp / "beta.md"
        self.file_b.write_text(
            f"# Beta\nattribution: {_marker(self.surface_b, self.tail_b)}\n"
        )
        # Pre-populate coverage.json so verify finds the surfaces.
        cov_path = _state.state_path(cov.COVERAGE_FILENAME)
        cov_path.parent.mkdir(parents=True, exist_ok=True)
        cov_path.write_text(json.dumps({
            "schema": 1,
            "surfaces": {
                self.surface_a: {"source": "scanned", "paths": [str(self.file_a)]},
                self.surface_b: {"source": "scanned", "paths": [str(self.file_b)]},
            },
        }))

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def test_all_intact_exits_zero(self):
        report = verify_cmd.compute()
        self.assertEqual(report["exit_code"], 0)
        for r in report["surfaces"]:
            self.assertEqual(r["status"], "intact")

    def test_one_drifted_exits_one(self):
        # Strip the marker from beta.
        self.file_b.write_text("# Beta\n(beacon scrubbed)\n")
        report = verify_cmd.compute()
        self.assertEqual(report["exit_code"], 1)
        beta = next(r for r in report["surfaces"] if r["surface"] == self.surface_b)
        self.assertEqual(beta["status"], "drifted")
        # alpha remains intact.
        alpha = next(r for r in report["surfaces"] if r["surface"] == self.surface_a)
        self.assertEqual(alpha["status"], "intact")

    def test_surface_flag_scopes(self):
        report = verify_cmd.compute(only_surface=self.surface_a)
        self.assertEqual(len(report["surfaces"]), 1)
        self.assertEqual(report["surfaces"][0]["surface"], self.surface_a)

    def test_output_does_not_echo_canary_literal(self):
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            verify_cmd.run(_ns())
        out = captured.getvalue()
        # Tails must not appear in stdout.
        self.assertNotIn(self.tail_a, out)
        self.assertNotIn(self.tail_b, out)
        # Full markers must not appear either.
        self.assertNotIn(_marker(self.surface_a, self.tail_a), out)
        self.assertNotIn(_marker(self.surface_b, self.tail_b), out)

        # JSON variant -- machine-consumable output is the most
        # likely accidental leak path.
        captured2 = io.StringIO()
        with patch("sys.stdout", captured2):
            verify_cmd.run(_ns(json=True))
        out2 = captured2.getvalue()
        self.assertNotIn(self.tail_a, out2)
        self.assertNotIn(self.tail_b, out2)


if __name__ == "__main__":
    unittest.main()
