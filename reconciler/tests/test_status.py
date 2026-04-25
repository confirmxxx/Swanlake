"""Tests for the status engine."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import status


class StatusComputeTest(unittest.TestCase):
    def test_fresh_when_sync_within_24h(self):
        now = datetime.now(timezone.utc)
        st = status._classify(now - timedelta(hours=1), now)
        self.assertEqual(st, 'fresh')

    def test_drift_yellow_when_24h_to_7d(self):
        now = datetime.now(timezone.utc)
        st = status._classify(now - timedelta(days=2), now)
        self.assertEqual(st, 'drift')

    def test_drift_red_when_over_7d(self):
        now = datetime.now(timezone.utc)
        st = status._classify(now - timedelta(days=10), now)
        self.assertEqual(st, 'drift-red')

    def test_missing_returns_missing(self):
        self.assertEqual(status._classify(None, datetime.now(timezone.utc)), 'missing')


class StatusReportTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)
        self.state = self.tmpdir / 'last-sync.json'

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_state_returns_all_missing(self):
        result = status.compute_report(self.state)
        for surface in ('claude_md', 'notion', 'vault'):
            self.assertEqual(result['surfaces'][surface]['status'], 'missing')

    def test_fresh_state_returns_all_fresh(self):
        now = datetime.now(timezone.utc)
        self.state.write_text(json.dumps({
            'claude_md': now.isoformat(),
            'notion': now.isoformat(),
            'vault': now.isoformat(),
        }))
        result = status.compute_report(self.state)
        for surface in ('claude_md', 'notion', 'vault'):
            self.assertEqual(result['surfaces'][surface]['status'], 'fresh')

    def test_overall_status_red_when_any_red(self):
        old = datetime.now(timezone.utc) - timedelta(days=10)
        now = datetime.now(timezone.utc)
        self.state.write_text(json.dumps({
            'claude_md': now.isoformat(),
            'notion': old.isoformat(),
            'vault': now.isoformat(),
        }))
        result = status.compute_report(self.state)
        self.assertEqual(result['overall'], 'drift-red')


if __name__ == '__main__':
    unittest.main()
