"""Tests for the status engine."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import acks, status


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


class StatePathDefaultTest(unittest.TestCase):
    """STATE_PATH must live under ~/.swanlake/ (post-v0.4.2 migration).

    The v0.4.1 fix made ``compute_report`` default ``acks_state_root`` to
    ``state_path.parent``. That made test-isolation work but it ALSO
    silently masked a production bug: when ``state_path`` defaulted to
    the legacy ``~/.config/swanlake-reconciler/last-sync.json``,
    ``state_path.parent`` resolved to the legacy XDG dir, NOT the
    ``~/.swanlake/`` root where ``reconciler.acks`` actually writes
    records. So ``swanlake reconciler ack`` succeeded but
    ``swanlake status`` never saw the ack -- the surface stayed
    permanently ``missing``.

    This test pins the invariant: ``STATE_PATH.parent`` must equal the
    same root ``reconciler.acks`` defaults to, so a default-path
    ``compute_report()`` call sees default-path acks.
    """

    def test_default_state_path_lives_under_swanlake_root(self):
        self.assertEqual(status.STATE_PATH, Path.home() / '.swanlake' / 'last-sync.json')

    def test_state_path_parent_matches_acks_state_root(self):
        # The whole point of the v0.4.2 migration: default state path's
        # parent must be the same dir reconciler.acks uses by default,
        # so compute_report's `acks_state_root = state_path.parent`
        # default lands on the right file in production.
        self.assertEqual(status.STATE_PATH.parent, acks.DEFAULT_STATE_ROOT)

    def test_legacy_path_constant_preserved(self):
        # Migration source must remain importable so future tooling can
        # reason about the legacy location without hardcoding the string.
        self.assertEqual(
            status._LEGACY_STATE_PATH,
            Path.home() / '.config' / 'swanlake-reconciler' / 'last-sync.json',
        )


class LegacyStateMigrationTest(unittest.TestCase):
    """One-shot migration copies legacy last-sync.json to the new path.

    The migration only fires when ``compute_report`` reads the DEFAULT
    state path AND the new file does not exist yet. Tests pin
    STATE_PATH and _LEGACY_STATE_PATH at module level via patch so the
    operator's real legacy file is never touched.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)
        self.new_path = self.tmpdir / 'new' / 'last-sync.json'
        self.legacy_path = self.tmpdir / 'legacy' / 'last-sync.json'
        self.legacy_path.parent.mkdir(parents=True)
        self._patches = [
            patch.object(status, 'STATE_PATH', self.new_path),
            patch.object(status, '_LEGACY_STATE_PATH', self.legacy_path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()

    def test_migrates_legacy_file_forward(self):
        ts = datetime.now(timezone.utc).isoformat()
        self.legacy_path.write_text(json.dumps({'vault': ts}))
        # Trigger the migration via the public read path.
        status._read_state(self.new_path)
        self.assertTrue(self.new_path.exists(),
                        'new state file should have been migrated forward')
        self.assertEqual(json.loads(self.new_path.read_text()), {'vault': ts})

    def test_migration_leaves_legacy_file_in_place(self):
        # The operator may have other tooling reading the legacy file
        # (ad-hoc scripts, shell aliases, etc.). v0.4.2 explicitly
        # promises NOT to delete the legacy file.
        ts = datetime.now(timezone.utc).isoformat()
        self.legacy_path.write_text(json.dumps({'vault': ts}))
        status._read_state(self.new_path)
        self.assertTrue(self.legacy_path.exists())

    def test_no_migration_when_new_file_already_exists(self):
        # Migration is one-shot: if the operator has already written to
        # the new path, the legacy file must NOT clobber it.
        new_ts = datetime.now(timezone.utc).isoformat()
        legacy_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.new_path.parent.mkdir(parents=True, exist_ok=True)
        self.new_path.write_text(json.dumps({'vault': new_ts}))
        self.legacy_path.write_text(json.dumps({'vault': legacy_ts}))
        status._read_state(self.new_path)
        # New file untouched.
        self.assertEqual(json.loads(self.new_path.read_text()), {'vault': new_ts})

    def test_no_migration_for_explicit_state_path_override(self):
        # Tests pass tempdir state paths; the migration must not run
        # against those because (a) the legacy file is unrelated and
        # (b) we don't want operator state leaking into a test.
        ts = datetime.now(timezone.utc).isoformat()
        self.legacy_path.write_text(json.dumps({'vault': ts}))
        explicit = self.tmpdir / 'explicit' / 'last-sync.json'
        explicit.parent.mkdir(parents=True)
        status._read_state(explicit)
        self.assertFalse(explicit.exists(),
                         'migration must only fire for the default STATE_PATH')

    def test_unparseable_legacy_file_skipped_silently(self):
        # A flaky filesystem could leave a half-written legacy file.
        # The migration must not propagate junk forward, and must not
        # crash the read path.
        self.legacy_path.write_text('this is not json {{{')
        result = status._read_state(self.new_path)
        # Falls back to all-missing semantics; no migration attempted.
        self.assertEqual(result, {})
        self.assertFalse(self.new_path.exists())


class AckSubcommandToStatusEndToEndTest(unittest.TestCase):
    """Regression test for the v0.4.1 bug: ack writes vs status reads.

    Reproduces the production failure mode: an ack recorded via
    ``reconciler.acks.write_ack`` (the same path the
    ``swanlake reconciler ack`` CLI uses) must be visible to
    ``status.compute_report`` when ``compute_report`` is called the
    way the live ``swanlake status`` CLI calls it -- which is to say,
    with the default ``acks_state_root`` (None) so that the engine
    derives the ack root from ``state_path.parent``.

    Pre-v0.4.2 this code path was broken because ``STATE_PATH`` lived
    at ``~/.config/swanlake-reconciler/`` while ``reconciler.acks``
    wrote to ``~/.swanlake/``. ``state_path.parent`` resolved to the
    legacy XDG dir, ``acks.latest_acks`` looked there, found nothing,
    and the surface reported ``missing`` despite a successful ack.

    The test pins the parent/child invariant in a tempdir: it places
    a fake ``last-sync.json`` into the same dir where it writes acks,
    then calls ``compute_report(state_path=...)`` with NO
    ``acks_state_root`` arg -- the same default the live CLI uses.
    The status engine must derive the correct ack root from the
    parent of the state path.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        # SWANLAKE_STATE_ROOT pins the env-var-aware acks default; both
        # the writer (write_ack) and any default-resolved reader share
        # the same root in production, so the test pins the same
        # invariant via env+tempdir.
        self._prior_env = os.environ.get('SWANLAKE_STATE_ROOT')
        os.environ['SWANLAKE_STATE_ROOT'] = str(self.root)
        self.state = self.root / 'last-sync.json'

    def tearDown(self):
        if self._prior_env is None:
            os.environ.pop('SWANLAKE_STATE_ROOT', None)
        else:
            os.environ['SWANLAKE_STATE_ROOT'] = self._prior_env
        self._tmpdir.cleanup()

    def test_ack_then_default_compute_report_sees_ack(self):
        # 1. Operator writes an ack the way the CLI subcommand does.
        acks.write_ack('notion', state_root=self.root)

        # 2. Status reader runs WITHOUT acks_state_root -- the production
        # code path. The engine must derive the ack root from
        # state_path.parent. Pre-v0.4.2 this would have crossed dir
        # boundaries (state in XDG, acks in ~/.swanlake) and reported
        # `missing`.
        report = status.compute_report(state_path=self.state)

        # 3. The ack must be visible. Asserting against a literal
        # "fresh" rather than just "not missing" pins the full
        # round-trip -- write + read + classify.
        notion = report['surfaces']['notion']
        self.assertEqual(
            notion['status'], 'fresh',
            f'ack written via reconciler.acks must be visible when '
            f'compute_report defaults acks_state_root from '
            f'state_path.parent (was: {notion!r})',
        )
        self.assertEqual(notion['synced_via'], 'ack')
        self.assertIsNotNone(notion['last_ack_utc'])

    def test_state_path_parent_invariant_is_load_bearing(self):
        """If STATE_PATH.parent ever drifts away from acks root, the
        end-to-end ack flow breaks again -- this test pins the link.

        This is the invariant that v0.4.2 restores: production
        STATE_PATH lives in the same dir as the acks JSONL, so the
        v0.4.1 ``acks_state_root = state_path.parent`` default does
        the right thing in production AND in tests.
        """
        # If someone changes STATE_PATH back to ~/.config/swanlake-reconciler/
        # without updating reconciler.acks, this assertion blows up.
        self.assertEqual(
            status.STATE_PATH.parent,
            acks.DEFAULT_STATE_ROOT,
            'STATE_PATH.parent must equal acks.DEFAULT_STATE_ROOT so '
            'that compute_report() with default acks_state_root reads '
            'the same JSONL that reconciler.acks writes to.',
        )


if __name__ == '__main__':
    unittest.main()
