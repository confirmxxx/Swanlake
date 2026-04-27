"""Pin the swanlake.exit_codes constants to their spec values.

The numeric values are load-bearing across the audit log, calling shells,
and the CLI spec. A typo here would silently change semantics for every
caller (cron / PR-bots / status-line shims). E28 in
docs/edge-case-audit-2026-04-27.md.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import exit_codes


class ExitCodePinTest(unittest.TestCase):
    def test_clean_is_zero(self) -> None:
        self.assertEqual(exit_codes.CLEAN, 0)

    def test_drift_is_one(self) -> None:
        self.assertEqual(exit_codes.DRIFT, 1)

    def test_alarm_is_two(self) -> None:
        self.assertEqual(exit_codes.ALARM, 2)

    def test_usage_collides_with_alarm(self) -> None:
        # argparse convention: usage errors exit 2. The spec accepts the
        # collision. Pin it so the collision is intentional, not accidental.
        self.assertEqual(exit_codes.USAGE, 2)
        self.assertEqual(exit_codes.USAGE, exit_codes.ALARM)

    def test_not_implemented_is_three(self) -> None:
        # Distinguishable from ALARM so callers can tell "feature missing"
        # apart from "alarm fired".
        self.assertEqual(exit_codes.NOT_IMPLEMENTED, 3)


if __name__ == "__main__":
    unittest.main()
