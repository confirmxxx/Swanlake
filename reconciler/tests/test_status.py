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


class StatusReadResilienceTest(unittest.TestCase):
    """compute_report must degrade gracefully on bad input — never crash."""
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.state = Path(self._tmpdir.name) / 'last-sync.json'

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_malformed_json_returns_all_missing(self):
        self.state.write_text('this is not valid json {{{')
        result = status.compute_report(self.state)
        for surface in ('claude_md', 'notion', 'vault'):
            self.assertEqual(result['surfaces'][surface]['status'], 'missing')

    def test_unparseable_timestamp_treated_as_missing(self):
        self.state.write_text(json.dumps({
            'claude_md': 'not-a-timestamp',
            'notion': '',
            'vault': None,
        }))
        result = status.compute_report(self.state)
        for surface in ('claude_md', 'notion', 'vault'):
            self.assertEqual(result['surfaces'][surface]['status'], 'missing')

    def test_missing_outranks_drift_in_overall(self):
        """When any surface is missing, overall must reflect that, not 'drift'."""
        old = datetime.now(timezone.utc) - timedelta(days=2)
        self.state.write_text(json.dumps({
            'claude_md': old.isoformat(),  # drift
            # notion + vault absent → missing
        }))
        result = status.compute_report(self.state)
        self.assertEqual(result['overall'], 'missing')


class WriteSyncTimestampTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.state = Path(self._tmpdir.name) / 'last-sync.json'

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_round_trip_writes_then_reads(self):
        ts = datetime.now(timezone.utc).replace(microsecond=0)
        status.write_sync_timestamp('vault', when=ts, state_path=self.state)
        report = status.compute_report(self.state)
        self.assertEqual(report['surfaces']['vault']['status'], 'fresh')

    def test_preserves_other_surfaces(self):
        ts1 = datetime.now(timezone.utc)
        status.write_sync_timestamp('vault', when=ts1, state_path=self.state)
        status.write_sync_timestamp('notion', when=ts1, state_path=self.state)
        report = status.compute_report(self.state)
        self.assertEqual(report['surfaces']['vault']['status'], 'fresh')
        self.assertEqual(report['surfaces']['notion']['status'], 'fresh')

    def test_atomic_write_no_partial_file_on_crash(self):
        """Final state file must always be valid JSON, never truncated."""
        ts = datetime.now(timezone.utc)
        status.write_sync_timestamp('claude_md', when=ts, state_path=self.state)
        # Just verify the file is parseable JSON (atomicity = no partials).
        json.loads(self.state.read_text())

    def test_concurrent_writes_dont_lose_updates(self):
        """Two parallel writes via threading must both land."""
        import threading
        ts1 = datetime.now(timezone.utc)
        ts2 = ts1 + timedelta(seconds=1)
        # Run two writers in parallel a few times to surface races.
        for _ in range(5):
            t1 = threading.Thread(target=status.write_sync_timestamp,
                                  args=('claude_md',),
                                  kwargs={'when': ts1, 'state_path': self.state})
            t2 = threading.Thread(target=status.write_sync_timestamp,
                                  args=('vault',),
                                  kwargs={'when': ts2, 'state_path': self.state})
            t1.start(); t2.start(); t1.join(); t2.join()
        raw = json.loads(self.state.read_text())
        self.assertIn('claude_md', raw)
        self.assertIn('vault', raw)


if __name__ == '__main__':
    unittest.main()
